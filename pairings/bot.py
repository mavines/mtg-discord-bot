# -*- coding: utf-8 -*-

import discord
import os
import random

from discord.ext import commands, tasks
from dotenv import load_dotenv
from pickledb import PickleDB
from swiss.match_log import MatchLog
from swiss.pairing_strategies.min_cost import pairings

load_dotenv()

ROUNDS = 3
ACTIVE = False

match_data = PickleDB('matches.db')
user_data  = PickleDB('users.db')

match_log = MatchLog()

# match_id is player_a + ' ' + player_b, forced alphabetical
def get_match(match_id):
    return match_data.get(match_id)

def lookup_match(pa, pb):
    pa = pa.lower()
    pb = pb.lower()
    if pa <= pb:
        return get_match(pa + ' ' + pb)
    return lookup_match(pb, pa)

# user_id is registered name, lowercase
def get_user(name):
    return user_data.get(name.lower())

def user_string(name):
    user = get_user(name)
    return f"{user['name']} ({user['match-wins']}-{user['match-losses']}) ({user['game-wins']}-{user['game-losses']})"

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='', intents=intents)

@bot.command()
async def register(ctx, *, args):
    name = args
    user_id = name.lower()
    # check if the user is already registered
    if get_user(user_id):
        await ctx.send('ERROR: Player already registered')
        return

    if len(name) < 1 or len(name.split()) != 1:
        await ctx.send('ERROR: You need to specify a name with no spaces\ne.g. !register Elspeth')
        return

    # registration looks good, so create and store a new user dict
    new_user = {
        'id': user_id,
        'name': name,
        'results': {},
        'match-wins': 0,
        'game-wins': 0,
        'match-losses': 0,
        'game-losses': 0
    }

    user_data.set(user_id, new_user)
    user_data.save()

    await ctx.send(f'{name} has been registered. Happy dueling!')

@bot.command()
async def stats(ctx):
    await ctx.send('\n'.join(map(lambda user_id:user_string(user_id), user_data.all())))

@bot.command()
async def begin(ctx):
    global ACTIVE
    if ACTIVE:
        await ctx.send('ERROR: Draft has already begun')
        return

    user_list = user_data.all()
    random.shuffle(user_list)
    for user_id in user_list:
        match_log.add_player(user_id)

    ret = 'Pairing initiated'
    # load match data, if it exists
    for match_id in match_data.all():
        (pa, pb) = match_id.split(' ')
        match = get_match(match_id)

        if match['pending']:
            match_log.set_player_active(pa, False)
            match_log.set_player_active(pb, False)
        else:
            match_log.add_result(pa, pb, match['wins_a'], match['wins_b'])

        ret = 'Match data loaded'

    ACTIVE = True
    await ctx.send(ret)

def match_string(match_id):
    (pa, pb) = match_id.split(' ')

    return f'{user_string(pa)} vs. {user_string(pb)}'

@bot.command()
async def pending(ctx):
    await ctx.send('\n'.join(map(match_string, filter(lambda match_id:get_match(match_id)['pending'] == True, match_data.all()))))

# inputs must be alphabetical
def result_helper(player_a, player_b, wins_a, wins_b):
    match = lookup_match(player_a, player_b)
    if not match:
        return 'Match not found'

    if wins_a > 2 or wins_b > 2:
        return 'Invalid score: greater than 2'

    if wins_a != 2 and wins_b != 2:
        return 'Invalid score: no 2'

    if wins_a == wins_b:
        return 'Invalid score: tied'

    match['pending'] = False
    match['wins_a'] = wins_a
    match['wins_b'] = wins_b

    user_a = get_user(player_a)
    user_b = get_user(player_b)

    user_a['game-wins'] += wins_a
    user_b['game-wins'] += wins_b

    user_a['game-losses'] += wins_b
    user_b['game-losses'] += wins_a

    if wins_a > wins_b:
        user_a['match-wins'] += 1
        user_b['match-losses'] += 1
    else:
        user_b['match-wins'] += 1
        user_a['match-losses'] += 1

    match_data.save()
    user_data.save()

    match_log.set_player_active(player_a, True)
    match_log.set_player_active(player_b, True)
    match_log.add_result(player_a, player_b, wins_a, wins_b)

    return 'Match recorded'

@bot.command()
async def result(ctx, player_a, player_b, wins_a:int, wins_b:int):
    player_a = player_a.lower()
    player_b = player_b.lower()

    ret = result_helper(player_a, player_b, wins_a, wins_b) if player_a < player_b else result_helper(player_b, player_a, wins_b, wins_a)
    await ctx.send(ret)

@tasks.loop(seconds=2)
async def attempt_draft():
    if not ACTIVE:
        return

    print('Checking for new pairings')
    current_pairings = pairings(match_log).pairs

    for pairing in current_pairings:
        if lookup_match(pairing.player_a, pairing.player_b):
            print('Rematch suggested, bailing')
            return

    info = []
    for pairing in current_pairings:
        pa = pairing.player_a
        pb = pairing.player_b

        # force alphabetical for db key
        if pa > pb:
            pa = pairing.player_b
            pb = pairing.player_a

        pairing_name = pa + ' ' + pb
        match_data.set(pairing_name, {'id': pairing_name, 'pending': True})

        info.append(f'Pairing: {user_string(pa)} vs. {user_string(pb)}')

        match_log.set_player_active(pa, False)
        match_log.set_player_active(pb, False)

        match_data.save()

    print('\n'.join(info))

@bot.event
async def on_ready():
    print('Bot is ready!')
    attempt_draft.start()

bot.run(os.getenv('DISCORD_TOKEN'))
