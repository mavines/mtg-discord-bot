# -*- coding: utf-8 -*-

import discord
import asyncio
import logging
import os
import random
import gspread # TODO: make the gspread calls in the async framework?

from datetime import datetime
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv
from math import floor
from pickledb import PickleDB

SEARCH_MAX_LENGTH = 5
WANTLIST_MAX_LENGTH = 20
DRAFT_ROUNDS = 10

load_dotenv()

card_data  = PickleDB('cards.db', False, False)
user_data  = PickleDB('users.db', False, False)
draft_data = PickleDB('draft.db', False, False)

# initialize db on fresh start
if not draft_data.get('number-of-rounds'):
    draft_data.set('number-of-rounds', DRAFT_ROUNDS)
    draft_data.set('has-started', False)
    draft_data.set('pick-number', 0)
    draft_data.set('pick-order', [])
    draft_data.dump()

    with open('card-list.txt', 'r') as f:
        for card_name in f.read().splitlines():
            card_id = card_name.lower()
            card_data.set(card_id, {'id': card_id, 'name': card_name, 'taken': False})

    card_data.dump()

def save_all():
    card_data.dump()
    user_data.dump()
    draft_data.dump()

# card_data is indexed by lowercase version of card name
def get_card(card_id):
    return card_data.get(card_id.lower())

# user_id is provided by discord and coerced to string by pickleDB
def get_user(user_id):
    return user_data.get(str(user_id))

def search_cards(fragment):
    fragment = fragment.lower()

    ret = []
    for card_id in card_data.getall():
        if fragment in card_id:
            ret.append(get_card(card_id))

        if len(ret) >= SEARCH_MAX_LENGTH:
            break

    return ret

def get_player_count():
    return len(draft_data.get('pick-order'))

def get_pick_number():
    return draft_data.get('pick-number')

def get_current_round():
    if get_player_count() == 0:
        return 0

    return floor(get_pick_number() / get_player_count())

def get_has_finished():
    return get_current_round() >= draft_data.get('number-of-rounds')

def get_current_index():
    player_count = get_player_count()
    pick_number_in_round = get_pick_number() % player_count

    # snaking forwards
    if get_current_round() % 2 == 0:
        return pick_number_in_round

    #snaking backwards
    return player_count - 1 - pick_number_in_round

def get_google_sheet(sheet_name):
    gc = gspread.service_account(filename='google-cloud-credentials.json')
    return gc.open_by_key(draft_data.get('google-sheet-id')).worksheet(sheet_name)

intents = discord.Intents.default()

class DraftBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        guild_id = os.getenv('DISCORD_GUILD_ID')
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        attempt_draft.start()

bot = DraftBot()

@bot.tree.error
async def command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    logging.error('Slash command failed', exc_info=(type(error), error, error.__traceback__))
    message = 'The command failed. Please contact the bot administrator.'
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)

@bot.tree.command(description='Register for the draft')
@app_commands.describe(team_name='Your team name (defaults to your Discord username)')
async def register(interaction: discord.Interaction, team_name: str = ''):
    # check if the user is already registered
    if get_user(interaction.user.id):
        await interaction.response.send_message('ERROR: You\'re already registered!')
        return

    # If the draft has been started, this command should be rejected
    if draft_data.get('has-started') == True:
        await interaction.response.send_message('ERROR: The draft has already been started.')
        return

    # default team_name to their discord username
    if len(team_name) == 0:
        team_name = interaction.user.name

    # Reject the user if the team name is already in use
    for user_id in user_data.getall():
        user = get_user(user_id)
        if team_name == user['team-name']:
            await interaction.response.send_message('ERROR: That team name is already in use! Please pick another one.')
            return

    # registration looks good, so create and store a new user dict
    new_user = {
        'discord-id': interaction.user.id,
        'discord-name': interaction.user.name,
        'discord-discriminator': interaction.user.discriminator,

        'team-name': team_name,
        'drafted-cards': [],
        'wanted-cards': []
    }

    user_data.set(str(interaction.user.id), new_user)
    user_data.dump()

    await interaction.response.send_message(f'Your team, {team_name}, has been registered. Happy dueling!')

@bot.tree.command(description='Start the draft after all players have registered')
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(google_sheet_id='Optional Google spreadsheet ID for a Lucky Paper draft template')
async def start_draft(interaction: discord.Interaction, google_sheet_id: str = ''):
    # if the draft has already finished, this command should be rejected
    if get_has_finished() == True:
        await interaction.response.send_message('ERROR: The draft already finished. To run a new draft, stop the bot and delete db files.')
        return

    # If the draft has been started, this command should be rejected
    if draft_data.get('has-started') == True:
        await interaction.response.send_message('ERROR: The draft has already been started.')
        return

    if not user_data.getall():
        await interaction.response.send_message('ERROR: Register at least one player before starting the draft.')
        return

    await interaction.response.defer(thinking=True)

    # get a random ordering of users
    pick_order = list(user_data.getall())
    random.shuffle(pick_order)

    # transform into team names for printing
    pick_order_names = list(map(lambda user_id:get_user(user_id)['team-name'], pick_order))

    pick_order_string = ', '.join(pick_order_names)

    if google_sheet_id:
        range_string = f'B12:B{12+len(pick_order)-1}'
        # generate list of lists because we are updating a vertical range
        player_names = list(map(lambda name:[name], pick_order_names))
        def publish_players():
            gc = gspread.service_account(filename='google-cloud-credentials.json')
            gc.open_by_key(google_sheet_id).worksheet('Overview').update(values=player_names, range_name=range_string)

        await asyncio.to_thread(publish_players)

    draft_data.set('google-sheet-id', google_sheet_id)
    draft_data.set('pick-order', pick_order)
    draft_data.set('has-started', True)
    draft_data.set('time-began', str(datetime.now()))
    draft_data.set('main-channel', interaction.channel_id)
    draft_data.set('current-drafter-notified', False)
    draft_data.dump()

    await interaction.followup.send(f'The draft has been started!\nThe draft order is: {pick_order_string}')

# currently allowing duplicates into the wantlist
# b/c would like to natively handle multiple copies of cards
@bot.tree.command(description='Queue a card to draft; unambiguous partial names are accepted')
@app_commands.describe(card_name='Card to add to the end of your draft queue')
async def draft(interaction: discord.Interaction, card_name: str):
    user = get_user(interaction.user.id)
    card = get_card(card_name)

    if not user:
        await interaction.response.send_message('ERROR: Not registered. Try the /register command')
        return

    # ensure a clear match before proceeding
    #   either an exact string match (in which case card is already populated)
    #   or use the card from search if there is exactly 1 match
    if not card:
        card_list = search_cards(card_name)

        if len(card_list) == 0:
            await interaction.response.send_message('ERROR: No matching cards found. Try /search to resolve')
            return

        if len(card_list) > 1:
            card_list_string = ', '.join(map(lambda card:card['name'], card_list))
            await interaction.response.send_message(f'ERROR: Multiple matching cards found. Please be precise\nMatches: {card_list_string}')
            return

        card = card_list[0]

    # if the draft has already finished, this command should be rejected
    if get_has_finished() == True:
        await interaction.response.send_message('ERROR: The draft already finished. To run a new draft, stop the bot and delete db files.')
        return

    # block excessive wantlist for efficiency purposes
    if len(user['wanted-cards']) >= WANTLIST_MAX_LENGTH:
        await interaction.response.send_message('ERROR: Your draft list is too long. Use /clear to wipe the list.')
        return

    # cannot want a card that has already been taken
    if card['taken']:
        await interaction.response.send_message('ERROR: Card already taken')
        return

    user['wanted-cards'].append(card['id'])
    user_data.dump()

    wanted_cards_string = ', '.join(map(lambda card_id:get_card(card_id)['name'], user['wanted-cards']))

    await interaction.response.send_message(f'Draftlist Updated: {wanted_cards_string}')

@bot.tree.command(description='Clear your draft queue')
async def clear(interaction: discord.Interaction):
    user = get_user(interaction.user.id)

    if not user:
        await interaction.response.send_message('ERROR: Not registered. Try the /register command')
        return

    user['wanted-cards'] = []
    user_data.dump()

    await interaction.response.send_message('Draftlist Wiped')

@bot.tree.command(description='Search for cards by full or partial name')
@app_commands.describe(card_name='Card name or part of a card name')
async def search(interaction: discord.Interaction, card_name: str):
    card_list = search_cards(card_name)

    if len(card_list):
        await interaction.response.send_message(', '.join(map(lambda card:card['name'], card_list)))
    else:
        await interaction.response.send_message(f'No cards found matching {card_name}')

@draft.autocomplete('card_name')
@search.autocomplete('card_name')
async def card_autocomplete(interaction: discord.Interaction, current: str):
    return [app_commands.Choice(name=card['name'][:100], value=card['name'])
            for card in search_cards(current)]

@tasks.loop(seconds=5)
async def attempt_draft():
    print('Attempting to perform draft')

    if get_has_finished():
        print('Draft is over')

        if not draft_data.get('current-drafter-notified'):
            channel = bot.get_channel(draft_data.get('main-channel')) or await bot.fetch_channel(draft_data.get('main-channel'))
            await channel.send('The draft is complete!')

            draft_data.set('current-drafter-notified', True)
            draft_data.dump()

        return

    if not draft_data.get('has-started'):
        print('Draft has not started')
        return

    current_drafter = get_user(draft_data.get('pick-order')[get_current_index()])
    want_list = current_drafter['wanted-cards']

    pick_string = f"Round {get_current_round()+1}, Pick {(get_pick_number() % get_player_count())+1}"

    if len(want_list) == 0:
        print('Waiting on current drafter')

        if not draft_data.get('current-drafter-notified'):
            player = bot.get_user(current_drafter['discord-id']) or await bot.fetch_user(current_drafter['discord-id'])
            try:
                await player.send(f"It's your turn to draft: {pick_string}")
            except discord.Forbidden:
                logging.warning('Cannot DM the current drafter; their DMs may be disabled.')

            draft_data.set('current-drafter-notified', True)
            draft_data.dump()

        return

    card_to_draft = get_card(want_list[0])
    assert card_to_draft['taken'] == False, 'Already taken card appears in want-list'

    if draft_data.get('google-sheet-id'):
        await asyncio.to_thread(
            lambda: get_google_sheet('Draft').update_cell(
                4 + get_current_round(), 3 + get_current_index(), card_to_draft['name']))

    # remove the card to draft from all want lists
    for user_id in user_data.getall():
        user = get_user(user_id)
        user['wanted-cards'] = list(filter(lambda card_id:card_id != card_to_draft['id'], user['wanted-cards']))

        # TODO snipe notifications go here if desired

    card_to_draft['taken'] = True
    current_drafter['drafted-cards'].append(card_to_draft['id'])

    info_string = f"{pick_string}\n{current_drafter['team-name']} has drafted {card_to_draft['name']}"

    print(info_string)

    draft_data.set('pick-number', draft_data.get('pick-number') + 1)
    draft_data.set('current-drafter-notified', False)

    save_all()

    channel = bot.get_channel(draft_data.get('main-channel')) or await bot.fetch_channel(draft_data.get('main-channel'))
    await channel.send(info_string)

@attempt_draft.before_loop
async def before_attempt_draft():
    await bot.wait_until_ready()

@bot.event
async def on_ready():
    print('Bot is ready!')

if __name__ == '__main__':
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        raise SystemExit('Set DISCORD_TOKEN in your environment or .env file.')
    bot.run(token)
