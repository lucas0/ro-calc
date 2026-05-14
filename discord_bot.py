#!/usr/bin/env python3
"""
RO MVP Tracker - Discord Bot
Watches a Discord channel for kill screenshots and auto-registers kills
to the tracker backend via OCR.

Setup:
  1. pip install -r requirements_bot.txt
  2. Edit bot_config.json (copy from bot_config.json.example)
  3. python discord_bot.py

First run downloads EasyOCR models (~500 MB from HuggingFace). Cached after that.
"""

import asyncio
import datetime
import difflib
import enum
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Optional

import discord
import easyocr
import requests

# ─── Config ───────────────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent / "bot_config.json"

if not CONFIG_PATH.exists():
    print(f"ERROR: {CONFIG_PATH} not found. Copy bot_config.json.example and fill it in.")
    sys.exit(1)

with open(CONFIG_PATH, encoding="utf-8") as _f:
    config = json.load(_f)

DISCORD_TOKEN   = config["discord_token"]
BOT_SECRET      = config["bot_secret"]
BACKEND_URL     = config.get("backend_url", "http://localhost:9500").rstrip("/")
TRACKER_URL     = config.get("tracker_url", "").rstrip("/")   # Public URL for the tracker web UI
GROUPS_CONFIG   = config.get("groups", [])
CONFIDENCE_MIN  = float(config.get("confidence_min", 0.5))
GPU_OCR         = bool(config.get("gpu_ocr", False))

# channel_id (int) → group_id (str)
CHANNEL_TO_GROUP: dict[int, str] = {}
for _g in GROUPS_CONFIG:
    for _ch in _g.get("channel_ids", []):
        CHANNEL_TO_GROUP[int(_ch)] = _g["group_id"]

# group_id (str) → list of channel_ids (int)  — used for bot-initiated messages (timers)
GROUP_TO_CHANNELS: dict[str, list[int]] = {}
for _g in GROUPS_CONFIG:
    GROUP_TO_CHANNELS[_g["group_id"]] = [int(_ch) for _ch in _g.get("channel_ids", [])]

# ─── Runtime config (mutable, persisted on change) ────────────────────────────
# Loaded from bot_config.json; slash commands update this dict and call _save_config().

_runtime_config: dict[str, dict] = {}

def _init_runtime_config() -> None:
    for g in GROUPS_CONFIG:
        gid = g["group_id"]
        kl  = g.get("kill_log") or {}
        fch = kl.get("forum_channel_id")
        _runtime_config[gid] = {
            "mention":          g.get("mention", "none"),
            "confidence_min":   float(g.get("confidence_min", CONFIDENCE_MIN)),
            "forum_channel_id": int(fch) if fch else None,
            "thread_suffix":    kl.get("thread_suffix", "MVP Kills"),
        }

_init_runtime_config()

def _save_config() -> None:
    """Write current runtime settings back to bot_config.json."""
    new_groups = []
    for g in config.get("groups", []):
        g2  = dict(g)
        rc  = _runtime_config.get(g2["group_id"], {})
        g2["mention"]        = rc.get("mention", "none")
        g2["confidence_min"] = rc.get("confidence_min", CONFIDENCE_MIN)
        kl  = dict(g2.get("kill_log") or {})
        kl["thread_suffix"]    = rc.get("thread_suffix", "MVP Kills")
        fch = rc.get("forum_channel_id")
        kl["forum_channel_id"] = str(fch) if fch else ""
        g2["kill_log"] = kl
        new_groups.append(g2)
    out = {**config, "groups": new_groups}
    with open(CONFIG_PATH, "w", encoding="utf-8") as _f:
        json.dump(out, _f, indent=2)

# ─── MVP Data (mirrored from index.html) ──────────────────────────────────────

MVP_DB = [
    {"id":"amon_ra",      "mid":1511, "name":"Amon Ra",             "maps":["moc_pryd06"],                                              "rMin":60,  "rWin":10, "drops":["Fragment of Rossata Stone","Safety Ring","Sphinx Hat","Old Card Album","Amon Ra Card"]},
    {"id":"atroce",       "mid":1785, "name":"Atroce",              "maps":["ra_fild02","ra_fild03","ra_fild04","ve_fild01","ve_fild02"],"rMin":240, "rWin":10, "drops":["Bloody Rune","Ring [1]","Yggdrasil Seed","Atroce Card"]},
    {"id":"baphomet",     "mid":1039, "name":"Baphomet",            "maps":["prt_maze03"],                                              "rMin":120, "rWin":10, "drops":["Crescent Scythe","Majestic Goat","Baphomet's Horn","Baphomet Card"]},
    {"id":"bacsojin",     "mid":1630, "name":"Bacsojin",            "maps":["lou_dun03"],                                               "rMin":117, "rWin":10, "drops":["Red Silk Seal","Tiara","Heavenly Maiden Robe [1]","White Lady Card"]},
    {"id":"dark_lord",    "mid":1272, "name":"Dark Lord",           "maps":["gl_chyard"],                                               "rMin":60,  "rWin":10, "drops":["Evil Bone Wand","Kronos","Mage Coat","Dark Lord Card"]},
    {"id":"doppelganger", "mid":1046, "name":"Doppelganger",        "maps":["gef_dun02"],                                               "rMin":120, "rWin":10, "drops":["Full Plate [1]","Spiky Band","Zweihander","Doppelganger Card"]},
    {"id":"dracula",      "mid":1389, "name":"Dracula",             "maps":["gef_dun01"],                                               "rMin":60,  "rWin":10, "drops":["Wizardry Staff","Ancient Cape","Ring [1]","Dracula Card"]},
    {"id":"drake",        "mid":1112, "name":"Drake",               "maps":["treasure02"],                                              "rMin":120, "rWin":10, "drops":["Saber [3]","Haedonggum [2]","Corsair","Drake Card"]},
    {"id":"eddga",        "mid":1115, "name":"Eddga",               "maps":["pay_fild10"],                                              "rMin":120, "rWin":10, "drops":["Fireblend","Pipe","Katar of Raging Blaze","Eddga Card"]},
    {"id":"evil_snake",   "mid":1418, "name":"Evil Snake Lord",     "maps":["gon_dun03"],                                               "rMin":94,  "rWin":10, "drops":["Grave Keeper's Sword","Gae Bolg","Ba Gua","Evil Snake Lord Card"]},
    {"id":"fbh",          "mid":1871, "name":"Fallen Bishop",       "maps":["abbey02"],                                                 "rMin":120, "rWin":10, "drops":["Wand of Occult","Long Horn [1]","Spiritual Ring","Fallen Bishop Card"]},
    {"id":"garm",         "mid":1252, "name":"Garm",                "maps":["xmas_fild01"],                                             "rMin":120, "rWin":10, "drops":["Ice Falchion","Katar of Frozen Icicle","Hatii Claw [1]","Hatii Card"]},
    {"id":"gtb",          "mid":1086, "name":"Golden Thief Bug",    "maps":["prt_sewb4"],                                               "rMin":60,  "rWin":10, "drops":["Gold","Golden Gear","Golden Mace [1]","GTB Card"]},
    {"id":"inc_samurai",  "mid":1492, "name":"Incantation Samurai", "maps":["ama_dun03"],                                               "rMin":91,  "rWin":10, "drops":["Huuma Blaze Shuriken","Assassin Mask","Azoth","Samurai Specter Card"]},
    {"id":"kiel",         "mid":1734, "name":"Kiel D-01",           "maps":["kh_dun02"],                                                "rMin":120, "rWin":10, "drops":["Morrigane's Pendant","Pocket Watch","Morrigane's Belt","Kiel-D-01 Card"]},
    {"id":"ktullanux",    "mid":1779, "name":"Ktullanux",           "maps":["ice_dun03"],                                               "rMin":120, "rWin":10, "drops":["Ice Scale","Sacred Mission","Survivor's Manteau","Ktullanux Card"]},
    {"id":"lady_tanee",   "mid":1688, "name":"Lady Tanee",          "maps":["ayo_dun02"],                                               "rMin":420, "rWin":10, "drops":["Gakkung Bow [2]","Banana Hat","Hwergelmir's Tonic","Lady Tanee Card"]},
    {"id":"lord_death",   "mid":1373, "name":"Lord of Death",       "maps":["niflheim"],                                                "rMin":133, "rWin":10, "drops":["Pole Axe [1]","Iron Driver","Ring [1]","Lord of the Dead Card"]},
    {"id":"maya",         "mid":1147, "name":"Maya",                "maps":["anthell02"],                                               "rMin":120, "rWin":10, "drops":["Queen's Hair Ornament","Safety Ring","Tiara","Maya Card"]},
    {"id":"mistress",     "mid":1059, "name":"Mistress",            "maps":["mjolnir_04"],                                              "rMin":120, "rWin":10, "drops":["Gungnir","Coronet","Old Card Album","Mistress Card"]},
    {"id":"moonlight",    "mid":1150, "name":"Moonlight Flower",    "maps":["pay_dun04"],                                               "rMin":60,  "rWin":10, "drops":["Spectral Spear","Moonlight Dagger","Punisher","Moonlight Flower Card"]},
    {"id":"orc_hero",     "mid":1087, "name":"Orc Hero",            "maps":["gef_fild14"],                                              "rMin":60,  "rWin":10, "drops":["Heroic Emblem","Light Epsilon","Giant Axe [1]","Orc Hero Card"]},
    {"id":"orc_lord",     "mid":1190, "name":"Orc Lord",            "maps":["gef_fild10"],                                              "rMin":120, "rWin":10, "drops":["Bloody Axe","Doom Slayer [1]","Grand Circlet","Orc Lord Card"]},
    {"id":"osiris",       "mid":1038, "name":"Osiris",              "maps":["moc_pryd04"],                                              "rMin":60,  "rWin":10, "drops":["Jamadhar [1]","Hand of God","Sphinx Hat","Osiris Card"]},
    {"id":"pharaoh",      "mid":1157, "name":"Pharaoh",             "maps":["in_sphinx5"],                                              "rMin":60,  "rWin":10, "drops":["Solar Sword","Jewel Crown","Holy Robe","Pharaoh Card"]},
    {"id":"phreeoni",     "mid":1159, "name":"Phreeoni",            "maps":["moc_fild17"],                                              "rMin":120, "rWin":10, "drops":["Fortune Sword","Sucsamad","Mr. Scream","Phreeoni Card"]},
    {"id":"stormy",       "mid":1251, "name":"Stormy Knight",       "maps":["xmas_dun02"],                                              "rMin":60,  "rWin":10, "drops":["Zephyrus","Manteau [1]","Ring [1]","Stormy Knight Card"]},
    {"id":"tao_gunka",    "mid":1583, "name":"Tao Gunka",           "maps":["beach_dun"],                                               "rMin":300, "rWin":10, "drops":["Gemstone","Binoculars","Gemmed Sallet [1]","Tao Gunka Card"]},
    {"id":"thanatos",     "mid":1708, "name":"Memory of Thanatos",  "maps":["thana_boss"],                                              "rMin":240, "rWin":10, "drops":["Memory of Thanatos Card","Skeletal Armor Piece","Morrigane's Manteau"]},
    {"id":"turtle_gen",   "mid":1312, "name":"Turtle General",      "maps":["tur_dun04"],                                               "rMin":60,  "rWin":10, "drops":["Iron Driver","Pole Axe [1]","Immaterial Sword","Turtle General Card"]},
    {"id":"vesper",       "mid":1685, "name":"Vesper",              "maps":["jupe_core"],                                               "rMin":120, "rWin":10, "drops":["Vesper Core 01","Vesper Core 02","Vesper Core 03","Vesper Core 04","Vesper Card"]},
    {"id":"beelzebub",    "mid":1873, "name":"Beelzebub",           "maps":["abbey03"],                                                 "rMin":720, "rWin":10, "drops":["Ledger of Death [2]","Staff of Destruction [1]","Variant Shoes","Beelzebub Card"]},
    {"id":"big_eggring",  "mid":3505, "name":"Big Eggring",         "maps":["lasa_dun01"],                                              "rMin":60,  "rWin":30, "drops":["Old Purple Box","Big Eggring Card"]},
    {"id":"boitata",      "mid":2068, "name":"Boitata",             "maps":["bra_dun02"],                                               "rMin":120, "rWin":10, "drops":["Flame Heart","Scarlet Odor","Boitata Card"]},
    {"id":"detale",       "mid":1719, "name":"Detardeurus",         "maps":["abyss_03"],                                                "rMin":180, "rWin":10, "drops":["Abyss Bow [2]","Dragon Breath","Detardeurus Card"]},
    {"id":"egnigem",      "mid":1658, "name":"Egnigem Cenia",       "maps":["lhz_dun02"],                                               "rMin":120, "rWin":10, "drops":["Safety Ring","Clip [1]","Egnigem Cenia Card"]},
    {"id":"gloom",        "mid":1768, "name":"Gloom Under Night",   "maps":["ra_san05"],                                                "rMin":300, "rWin":10, "drops":["Heavenly Maiden Robe [1]","Hurricane Fury [1]","Old Card Album","Gloom Under Night Card"]},
    {"id":"gopinich",     "mid":1885, "name":"Gopinich",            "maps":["mosk_dun03"],                                              "rMin":120, "rWin":10, "drops":["Treasure Box","Ixion Wing [1]","Gopinich Card"]},
    {"id":"ifrit",        "mid":1832, "name":"Ifrit",               "maps":["thor_v03"],                                                "rMin":660, "rWin":10, "drops":["Ring of Flame Lord","Ring of Resonance","Spiritual Ring","Ifrit Card"]},
    {"id":"leak",         "mid":2156, "name":"Leak",                "maps":["dew_dun01"],                                               "rMin":120, "rWin":10, "drops":["Old Purple Box","Leak Card"]},
    {"id":"queen_scaraba","mid":2087, "name":"Queen Scaraba",       "maps":["dic_dun02"],                                               "rMin":120, "rWin":10, "drops":["Alca Bringer","Meteo Plate Armor [1]","Queen Scaraba Card"]},
    {"id":"randgris",     "mid":1751, "name":"Valkyrie Randgris",   "maps":["odin_tem03"],                                              "rMin":480, "rWin":10, "drops":["Valkyrian Armor [1]","Valkyrian Manteau [1]","Valkyrian Shoes [1]","Randgris Card"]},
    {"id":"rsx",          "mid":1623, "name":"RSX-0806",            "maps":["ein_dun02"],                                               "rMin":125, "rWin":10, "drops":["Dark Blinder","Yggdrasil Berry","RSX-0806 Card"]},
    {"id":"wounded_morocc","mid":1917,"name":"Wounded Morocc",      "maps":["moc_fild22"],                                              "rMin":720, "rWin":60, "drops":["Diabolus Robe [1]","Diabolus Armor [1]","Diabolus Boots","Wounded Morocc Card"]},
    # Bio3 slot
    {"id":"bio3_slot",    "mid":1649, "name":"Bio3 Biolab",         "maps":["lhz_dun03"],                                               "rMin":120, "rWin":30,
     "drops":["Safety Ring","Heavenly Maiden Robe [1]","Critical Ring","Glittering Jacket [1]","Edge","Greaves [1]","Combat Knife","Grimtooth","Mysteltainn","Sabbath","Infiltrator","Bloody Roar [2]"],
     "bio3": True},
    # Bio4 slot
    {"id":"bio4_slot",    "mid":2228, "name":"Bio4 Biolab",         "maps":["lhz_dun04"],                                               "rMin":120, "rWin":30,
     "drops":["High Weapon Box","S Grade Coin Bag","Mystical Card Album","Bradium","Carnium"],
     "bio4": True},
]

# Individual Bio3 sub-MVPs (for specific identification)
BIO3_MVPS = [
    {"id":"lk_seyren",   "mid":1646, "name":"Lord Knight Seyren",    "bio3":True, "drops":["Old Purple Box","Old Blue Box","3carat Diamond","Edge","Brionac","Longinus's Spear","Dragon Slayer","Brocca","Legion Plate Armor","Greaves [1]","Lord Knight Card"]},
    {"id":"assx_eremes", "mid":1647, "name":"Assassin Cross Eremes", "bio3":True, "drops":["Old Purple Box","Old Blue Box","3carat Diamond","Moonlight Dagger","Ice Pick","Glittering Jacket [1]","Exorciser","Assassin Dagger","Bloody Roar [2]","Ginnungagap","Assassin Cross Card"]},
    {"id":"ws_harword",  "mid":1648, "name":"WS Harword",            "bio3":True, "drops":["Old Purple Box","Old Blue Box","3carat Diamond","Mysteltainn","Byeollungum","Lord's Clothes [1]","Sabbath","Great Axe","Guillotine","Tomahawk","Whitesmith Card"]},
    {"id":"hp_magaleta", "mid":1649, "name":"High Priest Magaleta",  "bio3":True, "drops":["Old Purple Box","Old Blue Box","3carat Diamond","Berserk","Heavenly Maiden Robe [1]","Safety Ring","Book of Apocalypse","Quadrille","Grand Cross","Sage's Diary","High Priest Card"]},
    {"id":"sniper",      "mid":1650, "name":"Sniper Shecil",         "bio3":True, "drops":["Old Purple Box","Old Blue Box","Combat Knife","Sucsamad","Moonlight Dagger","Grimtooth","Rudra Bow","Dragon Wing","Luna Bow","Sniper Card"]},
    {"id":"hw_katrinn",  "mid":1651, "name":"High Wizard Katrinn",   "bio3":True, "drops":["Old Purple Box","Old Blue Box","3carat Diamond","Cursed Dagger","Dagger of Counter","Glittering Jacket [1]","Robe of Cast","Safety Ring","Survivor's Rod","High Wizard Card"]},
]

# Individual Bio4 sub-MVPs (for specific identification)
BIO4_MVPS = [
    {"id":"b_randel",    "mid":2235, "name":"Paladin Randall",  "bio4":True, "drops":["High Weapon Box","S Grade Coin Bag","Mystical Card Album","Old Purple Box","Ghost Chill","Dead Branch","Bradium","Giant Shield [1]","Ancient Dagger","Paladin Card"]},
    {"id":"b_flamel",    "mid":2236, "name":"Creator Flamel",   "bio4":True, "drops":["High Weapon Box","S Grade Coin Bag","Mystical Card Album","Old Purple Box","Ghost Chill","Dead Branch","Carnium","End Sektura [1]","Ygnus Stale [1]","Giant Axe [1]","Creator Card"]},
    {"id":"b_celia",     "mid":2237, "name":"Professor Celia",  "bio4":True, "drops":["High Weapon Box","S Grade Coin Bag","Mystical Card Album","Old Purple Box","Ghost Chill","Dead Branch","Bradium","Alchemy Glove [1]","Professor Card"]},
    {"id":"b_chen",      "mid":2238, "name":"Champion Chen",    "bio4":True, "drops":["High Weapon Box","S Grade Coin Bag","Mystical Card Album","Old Purple Box","Ghost Chill","Dead Branch","Carnium","Chakram [2]","Kaiser Knuckle","Champion Card"]},
    {"id":"b_gertie",    "mid":2239, "name":"Stalker Gertie",   "bio4":True, "drops":["High Weapon Box","S Grade Coin Bag","Mystical Card Album","Old Purple Box","Ghost Chill","Dead Branch","Bradium","Scarletto Nail","Aztoe Nail","Stalker Card"]},
    {"id":"b_alphoccio", "mid":2240, "name":"Clown Alphoccio",  "bio4":True, "drops":["High Weapon Box","S Grade Coin Bag","Mystical Card Album","Old Purple Box","Ghost Chill","Dead Branch","Carnium","Mystic Bow","Clown Card"]},
    {"id":"b_trentini",  "mid":2241, "name":"Gypsy Trentini",  "bio4":True, "drops":["High Weapon Box","S Grade Coin Bag","Mystical Card Album","Old Purple Box","Ghost Chill","Dead Branch","Bradium","Rosevine","Mystic Bow","Gypsy Card"]},
]

# All MVPs used for identification scoring (individual bio MVPs, not slots)
_ALL_FOR_ID = MVP_DB + BIO3_MVPS + BIO4_MVPS

# ─── Item Database (mirrors ITEM_DB in index.html) ────────────────────────────
# Used to resolve OCR item names → item IDs for the website's image display

ITEM_DB: dict[int, str] = {
    501:  "Red Potion",            505:  "White Potion",
    506:  "Blue Potion",           607:  "Yggdrasil Seed",
    608:  "Condensed White Potion",
    617:  "Old Purple Box",        603:  "Old Blue Box",
    604:  "Dead Branch",           7049: "Old Blue Box (7049)",
    7142: "Old Violet Box",        732:  "3carat Diamond",
    12323:"High Weapon Box",
    984:  "Oridecon",              985:  "Elunium",
    756:  "Phracon",               757:  "Emveretarcon",
    7444: "Enriched Elunium",      7445: "Enriched Oridecon",
    7227: "Bradium",               7228: "Carnium",
    2640: "Orleans's Server",      2639: "Orleans's Glove",
    2411: "Proxy Skin Fragment",   2629: "Nile Rose",
    2615: "Safety Ring",           2616: "Critical Ring",
    2513: "Heavenly Maiden Robe [1]", 2318: "Lord's Clothes [1]",
    2319: "Glittering Jacket [1]", 2342: "Legion Plate Armor",
    2343: "Robe of Cast",          2412: "Greaves [1]",
    # Bio3 Lord Knight Seyren
    1132: "Edge",                  1470: "Brionac",
    1469: "Longinus's Spear",      1166: "Dragon Slayer",
    1415: "Brocca",                4357: "Lord Knight Card",
    # Bio3 Assassin Cross Eremes
    1234: "Moonlight Dagger",      1230: "Ice Pick",
    1233: "Exorciser",             1232: "Assassin Dagger",
    1265: "Bloody Roar [2]",       13002:"Ginnungagap",
    4359: "Assassin Cross Card",
    # Bio3 Whitesmith Harword
    1138: "Mysteltainn",           1140: "Byeollungum",
    1365: "Sabbath",               1364: "Great Axe",
    1369: "Guillotine",            1368: "Tomahawk",
    4361: "Whitesmith Card",
    # Bio3 High Priest Magaleta
    1814: "Berserk",               1557: "Book of Apocalypse",
    1527: "Quadrille",             1528: "Grand Cross",
    1560: "Sage's Diary",          4363: "High Priest Card",
    # Bio3 Sniper Shecil
    1236: "Sucsamad",              1237: "Grimtooth",
    1720: "Rudra Bow",             1724: "Dragon Wing",
    4367: "Sniper Card",           1723: "Luna Bow",
    1228: "Combat Knife",
    # Bio3 High Wizard Katrinn
    1241: "Cursed Dagger",         1242: "Dagger of Counter",
    1618: "Survivor's Rod",        4365: "High Wizard Card",
    # Bio4 shared
    12623:"High Weapon Box",       12616:"S Grade Coin Bag",
    6471: "Ghost Chill",           12246:"Mystical Card Album",
    6224: "Bradium",               6223: "Carnium",
    # Bio4 Paladin Randall
    2160: "Giant Shield [1]",      13062:"Ancient Dagger",
    4565: "Paladin Card",
    # Bio4 Creator Flamel
    1393: "End Sektura [1]",       1392: "Ygnus Stale [1]",
    1387: "Giant Axe [1]",         4563: "Creator Card",
    # Bio4 Professor Celia
    2854: "Alchemy Glove [1]",     4561: "Professor Card",
    # Bio4 Champion Chen
    1285: "Chakram [2]",           1813: "Kaiser Knuckle",
    4562: "Champion Card",
    # Bio4 Stalker Gertie
    13070:"Scarletto Nail",        13069:"Aztoe Nail",
    4564: "Stalker Card",
    # Bio4 Clown Alphoccio / Gypsy Trentini
    18103:"Mystic Bow",            4560: "Clown Card",
    1985: "Rosevine",              4566: "Gypsy Card",
}

# Reverse map: lower-case item name → item ID
_NAME_TO_ID: dict[str, int] = {name.lower(): item_id for item_id, name in ITEM_DB.items()}

# ─── Item Index (for fast MVP identification) ─────────────────────────────────

def _build_index() -> dict[str, list[str]]:
    idx: dict[str, list[str]] = {}
    for mvp in _ALL_FOR_ID:
        for drop in mvp["drops"]:
            key = drop.lower().strip()
            if key not in idx:
                idx[key] = []
            if mvp["id"] not in idx[key]:
                idx[key].append(mvp["id"])
    return idx

_ITEM_INDEX = _build_index()

# ─── Fuzzy item-name resolution ───────────────────────────────────────────────

def _fold(s: str) -> str:
    """Lowercase + collapse whitespace (not brackets) for comparison.
    "Legion Plate Armor [1]" → "legionplatearmor[1]"
    "LegionPlateArmor[1]"   → "legionplatearmor[1]"   (identical after fold)
    """
    return re.sub(r'\s+', '', s.lower())


def _build_item_cands() -> list[tuple[str, str]]:
    """(canonical_name, folded_name) for every known item drop + ITEM_DB entry."""
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for mvp in _ALL_FOR_ID:
        for drop in mvp.get("drops", []):
            if drop not in seen:
                seen.add(drop)
                result.append((drop, _fold(drop)))
    for name in ITEM_DB.values():
        if name not in seen:
            seen.add(name)
            result.append((name, _fold(name)))
    return result


_ITEM_CANDS: list[tuple[str, str]] = _build_item_cands()


def _clean_ocr_item(raw: str) -> str:
    """
    Light cleanup of a raw OCR item string — no fuzzy matching, just noise removal:
      "(1)" / "(4)" quantity suffixes, trailing '_.,;:' artifacts, extra whitespace.
    Item-to-item mapping happens later in build_merged_drops via Levenshtein.
    """
    s = re.sub(r'\s*\(\d+\)\s*$', '', raw)   # strip quantity "(qty)"
    s = s.rstrip('_.,;:')                     # trailing OCR noise chars
    s = re.sub(r'\s+', ' ', s).strip()
    return s or raw


def _levenshtein(a: str, b: str) -> int:
    """
    Wagner–Fischer edit distance (insertions, deletions, substitutions each cost 1).
    Runs in O(len(a) * len(b)) time and O(len(b)) space.
    """
    if a == b:
        return 0
    n, m = len(a), len(b)
    if not n: return m
    if not m: return n
    row = list(range(m + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(
                row[j] + 1,             # deletion
                row[j - 1] + 1,         # insertion
                prev + (ca != cb),       # substitution
            )
    return row[m]


def _closest_drop(raw_ocr: str, standard_drops: list[dict], threshold: float = 0.40) -> Optional[dict]:
    """
    Find which standard drop best matches a raw OCR item string.

    Compares each candidate on two folded representations to handle the two
    most common OCR failure modes:
      1. Trailing noise    — "Greaves [1]:"  vs "Greaves [1]"   → distance 0 after cleanup
      2. Missing spaces    — "LegionPlateArmor[1]" vs "Legion Plate Armor" → fold removes spaces
      3. Prefix truncation — "Longinus'"     vs "Longinus's Spear" → small relative distance

    threshold: accept match if (edit_distance / max_length) ≤ threshold.
               0.40 handles truncations up to ~40 % of the target length.

    Returns the best-matching drop dict, or None if nothing is close enough.
    """
    cleaned = _clean_ocr_item(raw_ocr).lower()
    if not cleaned:
        return None
    folded = _fold(cleaned)          # "greaves[1]", "legionplatearmor[1]", …

    best_drop  = None
    best_score = threshold + 1e-9   # lower is better; sentinel above threshold

    for drop in standard_drops:
        name   = drop["name"].lower()
        fname  = _fold(name)

        # Compute the relative edit distance on both the cleaned and folded strings.
        # Taking the min lets either representation "win":
        #   cleaned comparison: catches "Greaves [1]:" → "Greaves [1]"  (d=0 after cleanup, d=1 otherwise)
        #   folded comparison:  catches "LegionPlateArmor[1]" → "legionplatearmor" (spaces gone)
        d_clean  = _levenshtein(cleaned, name)   / max(len(cleaned), len(name),  1)
        d_folded = _levenshtein(folded,  fname)  / max(len(folded),  len(fname), 1)
        score    = min(d_clean, d_folded)

        if score < best_score:
            best_score = score
            best_drop  = drop

    return best_drop   # None when best_score > threshold


# ─── OCR ──────────────────────────────────────────────────────────────────────

_ocr_reader: Optional[easyocr.Reader] = None

def _get_reader() -> easyocr.Reader:
    global _ocr_reader
    if _ocr_reader is None:
        print("Initializing EasyOCR (first run downloads ~500 MB)...")
        _ocr_reader = easyocr.Reader(["en"], gpu=GPU_OCR, verbose=False)
        print("EasyOCR ready.")
    return _ocr_reader

# Pattern 1: "[Guild] CharName has acquired ItemName."
_DROP_RE = re.compile(
    r"(?:\[.*?\]\s*)?(.+?)\s+has\s+acquired\s+(.+?)\.?\s*$",
    re.IGNORECASE,
)

# Pattern 2: "You got ItemName (qty)."  — items the screenshot owner received
_YOU_GOT_RE = re.compile(
    r"you\s+got\s+(.+?)(?:\s*\(\d+\))?\s*\.?\s*$",
    re.IGNORECASE,
)

# Sentinel used to mark items that belong to the screenshot sender
_SENDER_CHAR = "__sender__"


def parse_image(image_path: str) -> list[dict]:
    """
    OCR an image and return list of {char, item} dicts.

    char is either:
      - a character name from a 'has acquired' line, or
      - __sender__  for items on 'You got …' lines (attributed to the poster).

    Item names receive only light cleanup here (_clean_ocr_item).
    The actual fuzzy mapping to canonical drop names happens later in
    build_merged_drops, where we know the specific MVP's drop list.
    """
    reader = _get_reader()
    lines = reader.readtext(image_path, detail=0, paragraph=False)
    results = []
    for raw in lines:
        line = raw.strip()

        # Pattern 1 — "[Guild] CharName has acquired ItemName."
        m = _DROP_RE.match(line)
        if m:
            char = m.group(1).strip()
            item = _clean_ocr_item(m.group(2).strip().rstrip("."))
            if char and item:
                results.append({"char": char, "item": item})
            continue

        # Pattern 2 — "You got ItemName (qty)." — items the screenshot sender received
        m = _YOU_GOT_RE.match(line)
        if m:
            item = _clean_ocr_item(m.group(1).strip().rstrip("."))
            if item:
                results.append({"char": _SENDER_CHAR, "item": item})

    return results

# ─── MVP Identification ────────────────────────────────────────────────────────

def identify_mvp(item_names: list[str]) -> tuple[Optional[dict], Optional[dict], float]:
    """
    Score each MVP against the observed item list.
    Returns (parent_mvp, specific_sub_mvp_or_None, confidence_score).
    Bio3/Bio4: parent is the slot (bio3_slot/bio4_slot), specific is the matched sub-MVP.
    """
    scores: dict[str, float] = {}

    for raw_name in item_names:
        key = raw_name.lower().strip()

        # 1. Exact match
        matching = _ITEM_INDEX.get(key)

        # 2. Folded match — catches run-together OCR like "LegionPlateArmor[1]"
        if not matching:
            fk = _fold(key)
            for db_key, mvp_ids in _ITEM_INDEX.items():
                if _fold(db_key) == fk:
                    matching = mvp_ids
                    break

        # 3. Substring fallback — catches partial reads like "Longinus'"
        if not matching:
            for db_key, mvp_ids in _ITEM_INDEX.items():
                if len(key) > 4 and (key in db_key or db_key in key):
                    matching = mvp_ids
                    break

        if matching:
            weight = 1.0 / len(matching)  # unique items score much higher
            for mvp_id in matching:
                scores[mvp_id] = scores.get(mvp_id, 0.0) + weight

    if not scores:
        return None, None, 0.0

    best_id    = max(scores, key=scores.get)
    best_score = scores[best_id]
    best_obj   = next((m for m in _ALL_FOR_ID if m["id"] == best_id), None)

    if best_obj is None:
        return None, None, 0.0

    # For Bio sub-MVPs, find their slot parent
    if best_obj.get("bio3"):
        parent = next((m for m in MVP_DB if m.get("bio3")), None)
        return parent, best_obj, best_score
    if best_obj.get("bio4"):
        parent = next((m for m in MVP_DB if m.get("bio4")), None)
        return parent, best_obj, best_score

    return best_obj, None, best_score

# ─── Character Name Matching ───────────────────────────────────────────────────

def match_char(ocr_char: str, members: list[dict]) -> Optional[dict]:
    """
    Match an OCR character name (may have [GUILD] prefix) to a tracker member.
    Tries exact match first, then partial.
    """
    # Strip leading [GUILD] tag
    name = re.sub(r"^\[.*?\]\s*", "", ocr_char).strip().lower()

    for m in members:
        if m.get("characterName", "").lower() == name:
            return m

    # Partial fallback (OCR may drop/add chars)
    for m in members:
        cname = m.get("characterName", "").lower()
        if len(name) >= 4 and (name in cname or cname in name):
            return m

    return None

# ─── Backend API ───────────────────────────────────────────────────────────────

_BOT_HEADERS = {"X-Bot-Secret": BOT_SECRET, "Content-Type": "application/json"}

def _bot_get(path: str) -> object:
    try:
        r = requests.get(f"{BACKEND_URL}{path}", headers=_BOT_HEADERS, timeout=10)
        return r.json()
    except Exception as exc:
        return {"error": str(exc)}

def _bot_post(path: str, payload: dict) -> dict:
    try:
        r = requests.post(f"{BACKEND_URL}{path}", json=payload, headers=_BOT_HEADERS, timeout=15)
        return r.json()
    except Exception as exc:
        return {"error": str(exc)}

_group_url_cache: dict[str, str] = {}   # group_id → full public URL with ?invite=

def _get_group_tracker_url(group_id: str) -> str:
    """
    Return the public tracker URL for this group with the auto-join invite code
    embedded as a ?invite=CODE query parameter.  The frontend already handles
    ?invite= by auto-joining the group on load (or pre-filling the invite field).
    Result is cached so we only hit the backend once per group per bot session.
    """
    if not TRACKER_URL:
        return ""
    if group_id in _group_url_cache:
        return _group_url_cache[group_id]
    try:
        result = _bot_get(f"/api/bot/group?groupId={group_id}")
        if isinstance(result, dict) and result.get("inviteCode"):
            url = f"{TRACKER_URL}?invite={result['inviteCode']}"
            _group_url_cache[group_id] = url
            return url
    except Exception as exc:
        print(f"[bot] Could not fetch invite code for {group_id}: {exc}")
    # Fallback: tracker homepage without invite code
    _group_url_cache[group_id] = TRACKER_URL
    return TRACKER_URL


def get_members(group_id: str) -> list[dict]:
    result = _bot_get(f"/api/bot/members?groupId={group_id}")
    return result if isinstance(result, list) else []

def register_kill(payload: dict) -> dict:
    return _bot_post("/api/bot/register-kill", payload)

def update_kill_party(kill_id: str, group_id: str, party: list[str]) -> dict:
    return _bot_put("/api/bot/update-kill", {"killId": kill_id, "groupId": group_id, "party": party})

def _bot_put(path: str, payload: dict) -> dict:
    try:
        r = requests.put(f"{BACKEND_URL}{path}", json=payload, headers=_BOT_HEADERS, timeout=15)
        return r.json()
    except Exception as exc:
        return {"error": str(exc)}

# ─── Standard drops helper ────────────────────────────────────────────────────

def get_standard_drops(parent_mvp: dict, specific_mvp: Optional[dict]) -> list[dict]:
    """
    Return the full standard drop list for this MVP as {name, itemId} dicts.
    Prefers the specific sub-MVP drops (Bio3/Bio4) over the parent slot drops.
    """
    source = specific_mvp if specific_mvp else parent_mvp
    result = []
    for drop_name in source.get("drops", []):
        item_id = _NAME_TO_ID.get(drop_name.lower())
        result.append({"name": drop_name, "itemId": item_id})
    return result


def build_merged_drops(
    standard_drops: list[dict],
    ocr_drops: list[dict],
    members: list[dict],
    sender_id: Optional[str],
) -> list[dict]:
    """
    Merge standard MVP drops with OCR-detected assignments.

    - Every standard drop is included (full picture, even unassigned ones).
    - Each OCR item is matched to the closest standard drop by Levenshtein distance
      before falling back to inserting a new entry.  This prevents duplicates like
      "Greaves [1]:" and "Greaves [1]" both appearing as separate rows.
    - OCR items with no close match (truly novel drops) are appended at the end.
    """
    # Only items visible in the screenshot are included.
    # standard_drops are used solely as a lookup table for canonical names/IDs —
    # they are NOT pre-populated into the result.
    drop_map: dict[str, dict] = {}

    for ocr in ocr_drops:
        raw_item = ocr["item"]

        # Resolve who kept this item
        if ocr["char"] == _SENDER_CHAR:
            taken_by = sender_id or ""         # "You got …" → always the sender
        else:
            owner    = match_char(ocr["char"], members)
            taken_by = owner["id"] if owner else (sender_id or "")

        # 1. Try exact key match against already-seen drops (deduplication)
        exact_key = _clean_ocr_item(raw_item).lower()
        if exact_key in drop_map:
            drop_map[exact_key]["takenBy"] = taken_by
            continue

        # 2. Levenshtein match against the MVP's own drop list to get the
        #    canonical name (e.g. "Greaves [1]:" → "Greaves [1]",
        #    "LegionPlateArmor[1]" → "Legion Plate Armor [1]").
        matched = _closest_drop(raw_item, standard_drops)
        if matched:
            canon_key = matched["name"].lower()
            if canon_key not in drop_map:
                drop_map[canon_key] = {"name": matched["name"], "itemId": matched.get("itemId"), "takenBy": ""}
            drop_map[canon_key]["takenBy"] = taken_by
            continue

        # 3. Truly novel item (not on the MVP's standard drop list)
        clean = _clean_ocr_item(raw_item)
        novel_key = clean.lower()
        if novel_key and novel_key not in drop_map:
            item_id = _NAME_TO_ID.get(novel_key)
            drop_map[novel_key] = {"name": clean, "itemId": item_id, "takenBy": taken_by}

    return list(drop_map.values())


# ─── Weekly forum-thread helpers ──────────────────────────────────────────────

def _week_thread_name(suffix: str, ref_dt: Optional[datetime.datetime] = None) -> str:
    """Return the forum-thread name for the Mon–Sun week that contains ref_dt."""
    d = ref_dt.date() if ref_dt else datetime.datetime.utcnow().date()
    monday = d - datetime.timedelta(days=d.weekday())   # always Monday
    sunday = monday + datetime.timedelta(days=6)
    return f"{monday.day:02d}/{monday.month:02d} to {sunday.day:02d}/{sunday.month:02d} — {suffix}"


async def get_or_create_week_thread(
    channel: discord.ForumChannel,
    suffix:  str,
    ref_dt:  Optional[datetime.datetime] = None,
) -> discord.Thread:
    """Return the matching weekly thread, creating it if it doesn't exist yet."""
    name = _week_thread_name(suffix, ref_dt)

    # 1. Check active (non-archived) threads first — fast path
    for t in channel.threads:
        if t.name == name:
            return t

    # 2. Search recently archived threads
    try:
        async for t in channel.archived_threads(limit=30):
            if t.name == name:
                return t   # discord auto-unarchives when we post to it
    except discord.HTTPException:
        pass

    # 3. Create a new thread for this week
    initial_msg = f"📅 **{name}**\nKill log — edit the tracker to adjust item assignments."
    result = await channel.create_thread(name=name, content=initial_msg)
    # discord.py 2.x may return a Thread or a ThreadWithMessage namedtuple
    return result if isinstance(result, discord.Thread) else result.thread


# ─── Message → Kill tracking (for edit support) ───────────────────────────────
# Maps Discord message_id → {kill_id, group_id} so edits can update the kill.
_kill_by_message: dict[int, dict] = {}

# ─── Discord Client + Slash Commands ──────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree   = discord.app_commands.CommandTree(client)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _gid_for(interaction: discord.Interaction) -> Optional[str]:
    """Return the group_id for the channel where the slash command was used."""
    return CHANNEL_TO_GROUP.get(interaction.channel_id)  # type: ignore[arg-type]

async def _require_gid(interaction: discord.Interaction) -> Optional[str]:
    gid = _gid_for(interaction)
    if not gid:
        await interaction.response.send_message(
            "⚠️ This channel is not configured as an MVP watch channel.\n"
            "Add its ID to `channel_ids` in `bot_config.json`.",
            ephemeral=True,
        )
    return gid

# ── Command group ──────────────────────────────────────────────────────────────

mvpbot = discord.app_commands.Group(
    name="mvpbot",
    description="Configure and inspect the MVP Tracker bot",
)

# ── /mvpbot help ──────────────────────────────────────────────────────────────

@mvpbot.command(name="help", description="How to use the MVP Tracker bot")
async def cmd_help(interaction: discord.Interaction) -> None:
    tracker = TRACKER_URL or "https://ro-calc.com/mvp"

    # ── Page 1: how to register kills ─────────────────────────────────────
    e1 = discord.Embed(
        title="📖 MVP Tracker Bot — How to Use  (1/2)",
        description=(
            "The bot watches this channel for kills and keeps a live spawn timer "
            f"on **[the tracker website]({tracker})**."
        ),
        color=0x5865F2,
    )
    e1.add_field(
        name="📸  Register a kill from a screenshot",
        value=(
            "Tag the bot and attach a **loot screenshot** showing the "
            "\"*has acquired*\" notification messages.\n"
            "```@MVPBot  [+ screenshot attachment]```"
            "The bot OCR-reads the items, identifies the MVP, registers the kill "
            "and starts the spawn timer automatically.\n"
            "Tag teammates in the same message to add them to the party."
        ),
        inline=False,
    )
    e1.add_field(
        name="✍️  Register a kill without a screenshot",
        value=(
            "Tag the bot with the MVP name (typos are fine — fuzzy matching is used).\n"
            "```@MVPBot Baphomet @teammate1 @teammate2```"
            "The kill is registered with no drops. "
            "Useful when the MVP was stolen, or you forgot to screenshot."
        ),
        inline=False,
    )
    e1.add_field(
        name="🔔  Spawn timer notifications",
        value=(
            "After every registered kill the bot automatically sends alerts:\n"
            "🔘 **30 min** before the window opens\n"
            "🟠 **5 min** before the window opens\n"
            "🟡 **Window open** — MVP can spawn any moment\n"
            "🟢 **Alive** — past the window close, MVP is definitely up"
        ),
        inline=False,
    )
    e1.add_field(
        name="🌐  Join the group tracker",
        value=(
            "Every kill notification has a clickable title that opens the tracker.\n"
            "The link includes your group's invite code — **just log in with Discord "
            "and you are added to the group automatically.**\n"
            f"Direct link: {tracker}"
        ),
        inline=False,
    )
    e1.set_footer(text="Use /mvpbot status to see this channel's current settings.")

    # ── Page 2: configuration commands ────────────────────────────────────
    e2 = discord.Embed(
        title="⚙️ MVP Tracker Bot — Configuration  (2/2)",
        description="These commands require the **Manage Server** permission.",
        color=0x5865F2,
    )
    e2.add_field(
        name="/mvpbot status",
        value="Show the current configuration for this channel's group.",
        inline=False,
    )
    e2.add_field(
        name="/mvpbot mention  `preset`  `[role]`",
        value=(
            "Set who gets pinged when a kill or timer alert fires.\n"
            "`@here` · `@everyone` · `role` *(select a role)* · `none`"
        ),
        inline=False,
    )
    e2.add_field(
        name="/mvpbot confidence  `value`",
        value=(
            "Minimum OCR confidence to auto-register from a screenshot (0.0 – 1.0, default `0.5`).\n"
            "**Lower** → registers more kills but risks false positives.\n"
            "**Higher** → only very clear screenshots are accepted."
        ),
        inline=False,
    )
    e2.add_field(
        name="/mvpbot log-channel  `channel`",
        value=(
            "Set a **forum channel** for automatic weekly kill-log threads.\n"
            "One thread per Mon–Sun week is created automatically."
        ),
        inline=False,
    )
    e2.add_field(
        name="/mvpbot thread-suffix  `text`",
        value=(
            "Suffix appended to the weekly thread name.\n"
            "Example: `Bio MVPs` → thread named `14/04 to 20/04 — Bio MVPs`"
        ),
        inline=False,
    )
    e2.set_footer(text="Settings are saved immediately and persist across bot restarts.")

    await interaction.response.send_message(embeds=[e1, e2], ephemeral=True)

# ── /mvpbot status ─────────────────────────────────────────────────────────────

@mvpbot.command(name="status", description="Show the current bot configuration for this channel")
async def cmd_status(interaction: discord.Interaction) -> None:
    gid = await _require_gid(interaction)
    if not gid:
        return
    rc  = _runtime_config.get(gid, {})
    fch = rc.get("forum_channel_id")
    watch_channels = [cid for cid, g in CHANNEL_TO_GROUP.items() if g == gid]

    e = discord.Embed(title="⚙️ MVPBot — Current Configuration", color=0x5865F2)
    e.add_field(name="🔔 Mention on kill",  value=f"`{rc.get('mention', 'none')}`",          inline=True)
    e.add_field(name="🎯 Min confidence",   value=f"`{rc.get('confidence_min', 0.5):.0%}`", inline=True)
    e.add_field(name="📅 Log channel",      value=f"<#{fch}>" if fch else "_not set_",       inline=True)
    e.add_field(name="🏷 Thread suffix",    value=f"`{rc.get('thread_suffix', 'MVP Kills')}`", inline=True)
    e.add_field(
        name="📡 Watching channels",
        value=" ".join(f"<#{c}>" for c in watch_channels) or "_none_",
        inline=False,
    )
    await interaction.response.send_message(embed=e, ephemeral=True)

# ── /mvpbot mention ────────────────────────────────────────────────────────────

class _MentionPreset(enum.Enum):
    here     = "@here"
    everyone = "@everyone"
    role     = "role"
    none     = "none"

@mvpbot.command(name="mention", description="Set who gets pinged when a kill is auto-registered in this channel")
@discord.app_commands.describe(
    preset="Choose @here, @everyone, a specific role, or none",
    role='The role to ping — only required when preset is "role"',
)
@discord.app_commands.checks.has_permissions(manage_guild=True)
async def cmd_mention(
    interaction: discord.Interaction,
    preset: _MentionPreset,
    role: Optional[discord.Role] = None,
) -> None:
    gid = await _require_gid(interaction)
    if not gid:
        return

    if preset == _MentionPreset.role:
        if not role:
            await interaction.response.send_message(
                '⚠️ You chose preset `role` but did not pick a role. '
                'Use the `role` parameter to select one.',
                ephemeral=True,
            )
            return
        tag = role.mention   # "<@&123456789>"
        label = f"{role.name} role"
    else:
        tag   = preset.value  # "@here", "@everyone", or "none"
        label = tag

    _runtime_config[gid]["mention"] = tag
    _save_config()
    if tag == "none":
        await interaction.response.send_message("✅ Kill announcements will be silent (no ping).", ephemeral=True)
    else:
        await interaction.response.send_message(
            f"✅ Kill announcements will now ping **{label}**.", ephemeral=True
        )

@cmd_mention.error
async def cmd_mention_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError) -> None:
    if isinstance(error, discord.app_commands.MissingPermissions):
        await interaction.response.send_message(
            "⛔ You need the **Manage Server** permission to change bot settings.", ephemeral=True
        )

# ── /mvpbot confidence ─────────────────────────────────────────────────────────

@mvpbot.command(name="confidence", description="Set the minimum OCR confidence required to auto-register a kill")
@discord.app_commands.describe(value="Number between 0.0 and 1.0 (default 0.5)")
@discord.app_commands.checks.has_permissions(manage_guild=True)
async def cmd_confidence(interaction: discord.Interaction, value: float) -> None:
    gid = await _require_gid(interaction)
    if not gid:
        return
    if not (0.0 <= value <= 1.0):
        await interaction.response.send_message(
            "⚠️ Value must be between `0.0` and `1.0`.", ephemeral=True
        )
        return
    _runtime_config[gid]["confidence_min"] = value
    _save_config()
    await interaction.response.send_message(
        f"✅ Confidence threshold set to **{value:.0%}**.", ephemeral=True
    )

@cmd_confidence.error
async def cmd_confidence_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError) -> None:
    if isinstance(error, discord.app_commands.MissingPermissions):
        await interaction.response.send_message(
            "⛔ You need the **Manage Server** permission to change bot settings.", ephemeral=True
        )

# ── /mvpbot log-channel ────────────────────────────────────────────────────────

@mvpbot.command(name="log-channel", description="Set the forum channel where weekly kill-log threads are created")
@discord.app_commands.describe(channel="A forum channel — one Mon–Sun thread is created automatically per week")
@discord.app_commands.checks.has_permissions(manage_guild=True)
async def cmd_log_channel(interaction: discord.Interaction, channel: discord.ForumChannel) -> None:
    gid = await _require_gid(interaction)
    if not gid:
        return
    _runtime_config[gid]["forum_channel_id"] = channel.id
    _save_config()
    await interaction.response.send_message(
        f"✅ Weekly kill logs will be posted to {channel.mention}.", ephemeral=True
    )

@cmd_log_channel.error
async def cmd_log_channel_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError) -> None:
    if isinstance(error, discord.app_commands.MissingPermissions):
        await interaction.response.send_message(
            "⛔ You need the **Manage Server** permission to change bot settings.", ephemeral=True
        )

# ── /mvpbot thread-suffix ─────────────────────────────────────────────────────

@mvpbot.command(name="thread-suffix", description="Set the suffix used in weekly kill-log thread names")
@discord.app_commands.describe(
    suffix='Text after the date range, e.g. "Bio MVPs" → "13/04 to 19/04 — Bio MVPs"'
)
@discord.app_commands.checks.has_permissions(manage_guild=True)
async def cmd_thread_suffix(interaction: discord.Interaction, suffix: str) -> None:
    gid = await _require_gid(interaction)
    if not gid:
        return
    suffix = suffix.strip()
    if not suffix:
        await interaction.response.send_message("⚠️ Suffix cannot be empty.", ephemeral=True)
        return
    _runtime_config[gid]["thread_suffix"] = suffix
    _save_config()
    example = _week_thread_name(suffix)
    await interaction.response.send_message(
        f"✅ Thread suffix updated.\nExample thread name: **{example}**", ephemeral=True
    )

@cmd_thread_suffix.error
async def cmd_thread_suffix_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError) -> None:
    if isinstance(error, discord.app_commands.MissingPermissions):
        await interaction.response.send_message(
            "⛔ You need the **Manage Server** permission to change bot settings.", ephemeral=True
        )

# Register the command group with the tree
tree.add_command(mvpbot)

# ─── Manual kill (no screenshot) ─────────────────────────────────────────────

def find_mvp_by_name(query: str) -> tuple[Optional[dict], Optional[dict], float]:
    """
    Fuzzy-match a user's text to an MVP name.
    Uses SequenceMatcher ratio + substring boost.
    Returns (parent_mvp, specific_sub_mvp_or_None, score).
    Bio3/Bio4 sub-MVPs resolve to their parent slot.
    """
    q = query.lower().strip()
    if not q:
        return None, None, 0.0

    best_score = 0.0
    best_mvp   = None

    for mvp in _ALL_FOR_ID:
        name  = mvp["name"].lower()
        score = difflib.SequenceMatcher(None, q, name).ratio()
        # Boost when one string is a substring of the other
        if q in name or name in q:
            score = max(score, 0.70)
        if score > best_score:
            best_score = score
            best_mvp   = mvp

    if not best_mvp or best_score < 0.45:
        return None, None, 0.0

    # Resolve bio sub-MVPs → parent slot
    if best_mvp.get("bio3"):
        parent = next((m for m in MVP_DB if m.get("bio3")), None)
        return parent, best_mvp, best_score
    if best_mvp.get("bio4"):
        parent = next((m for m in MVP_DB if m.get("bio4")), None)
        return parent, best_mvp, best_score

    return best_mvp, None, best_score


async def _handle_manual_kill(message: discord.Message, group_id: str) -> None:
    """
    Register a kill without a screenshot.
    Usage: @bot <MVP name> [@party_member ...]
    """
    # Strip all @mentions from the message to isolate the MVP name query
    mvp_query = re.sub(r"<@[!&]?\d+>", "", message.content)
    mvp_query = re.sub(r"\s+", " ", mvp_query).strip()

    if not mvp_query:
        await message.reply(
            "⚠️ Tag me with an MVP name to register a kill without a screenshot.\n"
            "**Example:** `@MVPBot Baphomet @party_member`\n"
            "To register a screenshotted kill, attach the loot image to your message.",
            mention_author=False,
        )
        return

    parent_mvp, specific_mvp, score = find_mvp_by_name(mvp_query)
    if not parent_mvp:
        await message.reply(
            f"⚠️ Couldn't find an MVP matching **{mvp_query}**.\n"
            "Check the name and try again.",
            mention_author=False,
        )
        return

    await message.add_reaction("⏳")
    try:
        loop = asyncio.get_event_loop()

        members = await loop.run_in_executor(None, get_members, group_id)
        if not members:
            await _fail(message, "Could not fetch group members from tracker backend. Is it running?")
            return

        by_discord_id = {str(m["discordId"]): m for m in members if m.get("discordId")}

        sender_member = by_discord_id.get(str(message.author.id))
        sender_id     = sender_member["id"] if sender_member else None

        # Party = all @mentioned users except the bot itself
        party_ids: list[str] = []
        for user in message.mentions:
            if user.id == client.user.id:
                continue
            m = by_discord_id.get(str(user.id))
            if m and m["id"] not in party_ids:
                party_ids.append(m["id"])
        if sender_id and sender_id not in party_ids:
            party_ids.insert(0, sender_id)

        display_name = specific_mvp["name"] if specific_mvp else parent_mvp["name"]
        display_mid  = specific_mvp["mid"]  if specific_mvp else parent_mvp["mid"]
        map_id       = (parent_mvp.get("maps") or [""])[0]
        specific_id  = specific_mvp["id"] if specific_mvp else None

        if parent_mvp.get("bio3"):
            timer_key = "bio3::lhz_dun03"
        elif parent_mvp.get("bio4"):
            timer_key = "bio4::lhz_dun04"
        else:
            timer_key = f"{parent_mvp['id']}::{map_id}"

        death_time = int(message.created_at.timestamp() * 1000)

        payload = {
            "groupId":        group_id,
            "mvpId":          parent_mvp["id"],
            "mvpName":        parent_mvp["name"],
            "map":            map_id,
            "deathTime":      death_time,
            "party":          party_ids,
            "drops":          [],
            "rMin":           parent_mvp.get("rMin", 60),
            "rWin":           parent_mvp.get("rWin", 10),
            "mid":            display_mid,
            "specificMvpId":  specific_id,
            "timerKey":       timer_key,
            "registeredById": sender_id,
        }

        result = await loop.run_in_executor(None, register_kill, payload)
        if result.get("error"):
            await _fail(message, f"Backend error: `{result['error']}`")
            return

        kill_id = result.get("killId")
        if kill_id:
            _kill_by_message[message.id] = {"kill_id": kill_id, "group_id": group_id}

        try:
            await message.clear_reaction("⏳")
        except discord.HTTPException:
            pass
        await message.add_reaction("✅")

        party_names = [m["characterName"] for m in members if m["id"] in party_ids]
        rc          = _runtime_config.get(group_id, {})
        mention_tag = rc.get("mention", "none")
        mention_str = "" if mention_tag.lower() == "none" else f"{mention_tag} "
        group_url   = await loop.run_in_executor(None, _get_group_tracker_url, group_id)

        embed = discord.Embed(
            title=f"⚔️ {display_name} kill registered!",
            url=group_url or None,
            color=0x00C853,
            timestamp=message.created_at,
        )
        embed.add_field(name="🗺 Map",    value=f"`{map_id}`",                                         inline=True)
        embed.add_field(name="⏱ Timer",  value=f"Next spawn in ≥ **{parent_mvp.get('rMin', '?')} min**", inline=True)
        embed.add_field(name="👥 Party",  value=", ".join(party_names) or "_(not resolved)_",          inline=False)
        embed.add_field(name="💎 Drops",  value="_(none — registered manually without screenshot)_",   inline=False)
        embed.set_thumbnail(url=f"https://static.divine-pride.net/images/mobs/png/{display_mid}.png")
        embed.set_footer(
            text=f"Manually registered by {message.author.display_name}"
                 f"  •  matched '{mvp_query}' → {display_name} ({score:.0%})"
        )
        await message.reply(content=mention_str or None, embed=embed)

        # Post to weekly forum thread if configured
        forum_channel_id = rc.get("forum_channel_id")
        thread_suffix    = rc.get("thread_suffix", "MVP Kills")
        if forum_channel_id:
            try:
                forum_ch = client.get_channel(forum_channel_id)
                if isinstance(forum_ch, discord.ForumChannel):
                    week_thread = await get_or_create_week_thread(
                        forum_ch, thread_suffix, message.created_at
                    )
                    log_embed = discord.Embed(
                        title=f"⚔️ {display_name}",
                        color=0x5865F2,
                        timestamp=message.created_at,
                    )
                    log_embed.add_field(name="🗺 Map",   value=f"`{map_id}`",                   inline=True)
                    log_embed.add_field(name="👥 Party", value=", ".join(party_names) or "_(unresolved)_", inline=True)
                    log_embed.add_field(name="💎 Drops", value="_(manual — no drops)_",         inline=False)
                    log_embed.set_footer(text=f"By {message.author.display_name}  •  manual")
                    await week_thread.send(embed=log_embed)
            except Exception as log_exc:
                print(f"[bot] Kill-log thread post failed: {log_exc}")

    except Exception as exc:
        await _fail(message, f"Unexpected error: `{exc}`")
        import traceback
        traceback.print_exc()


# ─── Timer notification background task ───────────────────────────────────────

def _fetch_timer_notifications() -> list[dict]:
    """Poll the backend for pending timer notifications (blocking, run in executor)."""
    try:
        r = requests.get(
            f"{BACKEND_URL}/api/bot/timer-notifications",
            headers=_BOT_HEADERS,
            timeout=10,
        )
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as exc:
        print(f"[timer] Fetch error: {exc}")
        return []


async def _send_timer_notification(notif: dict) -> None:
    """Send a single timer notification to all channels configured for that group."""
    group_id  = notif.get("groupId", "")
    state     = notif.get("state", "")
    mvp_name  = notif.get("mvpName") or notif.get("mvpId") or "Unknown MVP"
    map_id    = notif.get("map", "")
    mid       = notif.get("mid")
    rmin      = int(notif.get("rMin", 0))
    rwin      = int(notif.get("rWin", 0))
    death_ms  = int(notif.get("deathTime", 0))
    coord_x   = notif.get("coordX") or 0
    coord_y   = notif.get("coordY") or 0

    # Spawn-window times (UTC)
    win_open_ms   = death_ms + rmin * 60_000
    win_close_ms  = win_open_ms + rwin * 60_000
    win_open_dt   = datetime.datetime.utcfromtimestamp(win_open_ms  / 1000)
    win_close_dt  = datetime.datetime.utcfromtimestamp(win_close_ms / 1000)
    window_line   = (
        f"**Window:** {win_open_dt.strftime('%H:%M')} – {win_close_dt.strftime('%H:%M')} UTC\n"
        f"`/navi {map_id} {coord_x}/{coord_y}`"
    )

    if state == "pre30":
        title        = f"[30 min] {mvp_name}"
        desc         = f"Spawning in ~30 minutes\n{window_line}"
        color        = 0x727272
        content_body = f"**{mvp_name}** on `{map_id}` spawns in ~30 minutes!"
    elif state == "pre5":
        title        = f"[5 min] {mvp_name}"
        desc         = f"Spawning in ~5 minutes\n{window_line}"
        color        = 0xFF8810
        content_body = f"**{mvp_name}** on `{map_id}` spawns in ~5 MINUTES! Get ready!"
    elif state == "window":
        title        = f"[Spawn Window] {mvp_name}"
        desc         = window_line
        color        = 0xF0C040
        content_body = f"**{mvp_name}** spawn window is open on `{map_id}`!"
    else:  # available
        title        = f"[ALIVE] {mvp_name}"
        desc         = window_line
        color        = 0x57F287
        content_body = f"**{mvp_name}** on `{map_id}` is **ALIVE** — hunt it down!"

    embed = discord.Embed(title=title, description=desc, color=color)
    embed.set_footer(text=f"RO MVP Tracker · {map_id}")
    if mid:
        embed.set_thumbnail(url=f"https://static.divine-pride.net/images/mobs/png/{mid}.png")

    rc          = _runtime_config.get(group_id, {})
    mention_tag = rc.get("mention", "none")
    mention_str = "" if mention_tag.lower() == "none" else f"{mention_tag} "
    content     = f"{mention_str}{content_body}"

    channels = GROUP_TO_CHANNELS.get(group_id, [])
    for channel_id in channels:
        channel = client.get_channel(channel_id)
        if channel and isinstance(channel, discord.TextChannel):
            try:
                await channel.send(content=content, embed=embed)
                print(f"[timer] Sent {state} for {mvp_name} to #{channel.name}")
            except discord.HTTPException as exc:
                print(f"[timer] Failed to send to channel {channel_id}: {exc}")
        else:
            print(f"[timer] Channel {channel_id} not found or not a text channel")


_timer_task_started = False

async def _timer_notification_loop() -> None:
    """Background task: poll backend for queued timer notifications every 60 s."""
    await client.wait_until_ready()
    print("[timer] Timer notification loop started.")
    while not client.is_closed():
        try:
            loop = asyncio.get_event_loop()
            notifications = await loop.run_in_executor(None, _fetch_timer_notifications)
            for notif in notifications:
                await _send_timer_notification(notif)
        except Exception as exc:
            print(f"[timer] Unexpected error: {exc}")
        await asyncio.sleep(60)


# ──────────────────────────────────────────────────────────────────────────────

@client.event
async def on_ready():
    global _timer_task_started
    watching = ", ".join(str(ch) for ch in CHANNEL_TO_GROUP)
    print(f"Bot online as {client.user}  |  Watching channel(s): {watching or '(none configured)'}")
    # Sync commands at guild level (instant) and clear global commands.
    # Registering at both scopes causes duplicates in the slash-command picker.
    for guild in client.guilds:
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
        print(f"Synced slash commands to guild: {guild.name} ({guild.id})")
    # Wipe the global registration so Discord shows each command only once.
    tree.clear_commands(guild=None)
    await tree.sync()
    print("Global commands cleared — using guild-level commands only.")
    # Start timer notification loop only once (on_ready fires again on reconnect)
    if not _timer_task_started:
        _timer_task_started = True
        asyncio.ensure_future(_timer_notification_loop())

@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    group_id = CHANNEL_TO_GROUP.get(message.channel.id)
    if not group_id:
        return

    # Only process when the bot is explicitly mentioned in the message.
    if client.user not in message.mentions:
        return

    # Only care about messages that include at least one image.
    # Fall back to filename extension when content_type is missing (can happen with
    # certain Discord clients or when the message_content intent is partially restricted).
    _IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}
    images = [
        a for a in message.attachments
        if (a.content_type and a.content_type.startswith("image/"))
        or (not a.content_type and Path(a.filename).suffix.lower() in _IMG_EXTS)
    ]
    if not images:
        # No screenshot — try to register kill manually from the MVP name in the text
        await _handle_manual_kill(message, group_id)
        return

    await message.add_reaction("⏳")

    try:
        loop = asyncio.get_event_loop()

        # Fetch group members from backend
        members = await loop.run_in_executor(None, get_members, group_id)
        if not members:
            await _fail(message, "Could not fetch group members from tracker backend. Is it running?")
            return

        by_discord_id = {str(m["discordId"]): m for m in members if m.get("discordId")}
        by_char_lower = {m["characterName"].lower(): m for m in members if m.get("characterName")}

        # Resolve message sender → tracker member
        sender_member = by_discord_id.get(str(message.author.id))
        sender_id     = sender_member["id"] if sender_member else None

        # Resolve @mentions → tracker member IDs (party list)
        party_ids: list[str] = []
        for user in message.mentions:
            member = by_discord_id.get(str(user.id))
            if member and member["id"] not in party_ids:
                party_ids.append(member["id"])
        # Ensure sender is included in party
        if sender_id and sender_id not in party_ids:
            party_ids.insert(0, sender_id)

        # OCR all attached images (run in thread-pool, non-blocking)
        all_drops: list[dict] = []
        for attachment in images:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name
            try:
                await attachment.save(tmp_path)
                drops = await loop.run_in_executor(None, parse_image, tmp_path)
                all_drops.extend(drops)
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        if not all_drops:
            await _fail(
                message,
                "No **'has acquired'** lines detected in the image.\n"
                "Make sure the screenshot shows the in-game loot notification messages.",
            )
            return

        item_names    = [d["item"] for d in all_drops]
        unique_items  = list(dict.fromkeys(item_names))   # deduplicated, order preserved

        # Identify which MVP was killed
        parent_mvp, specific_mvp, score = await loop.run_in_executor(
            None, identify_mvp, item_names
        )

        rc             = _runtime_config.get(group_id, {})
        confidence_min = rc.get("confidence_min", CONFIDENCE_MIN)

        if not parent_mvp or score < confidence_min:
            preview = ", ".join(f"`{i}`" for i in unique_items[:6])
            if len(unique_items) > 6:
                preview += f" +{len(unique_items) - 6} more"
            await _fail(
                message,
                f"Could not identify the MVP with enough confidence.\n"
                f"Items found: {preview}\n"
                f"Confidence: **{score:.0%}** (minimum: {confidence_min:.0%})\n"
                "Please register this kill manually in the tracker.",
            )
            return

        # Effective display info
        display_name = specific_mvp["name"] if specific_mvp else parent_mvp["name"]
        display_mid  = specific_mvp["mid"]  if specific_mvp else parent_mvp["mid"]
        map_id       = (parent_mvp.get("maps") or [""])[0]
        specific_id  = specific_mvp["id"] if specific_mvp else None

        # Timer key (matches frontend timerKey() logic)
        if parent_mvp.get("bio3"):
            timer_key = "bio3::lhz_dun03"
        elif parent_mvp.get("bio4"):
            timer_key = "bio4::lhz_dun04"
        else:
            timer_key = f"{parent_mvp['id']}::{map_id}"

        # Kill time = message timestamp
        death_time = int(message.created_at.timestamp() * 1000)

        # Build full drop list: all standard drops + OCR-detected assignments
        standard_drops = get_standard_drops(parent_mvp, specific_mvp)
        kill_drops = build_merged_drops(standard_drops, all_drops, members, sender_id)

        # Send to backend
        payload = {
            "groupId":        group_id,
            "mvpId":          parent_mvp["id"],
            "mvpName":        parent_mvp["name"],
            "map":            map_id,
            "deathTime":      death_time,
            "party":          party_ids,
            "drops":          kill_drops,
            "rMin":           parent_mvp.get("rMin", 60),
            "rWin":           parent_mvp.get("rWin", 10),
            "mid":            display_mid,
            "specificMvpId":  specific_id,
            "timerKey":       timer_key,
            "registeredById": sender_id,
        }

        result = await loop.run_in_executor(None, register_kill, payload)

        if result.get("error"):
            await _fail(message, f"Backend error: `{result['error']}`")
            return

        # ── Success ──────────────────────────────────────────────────────────
        kill_id = result.get("killId")
        if kill_id:
            _kill_by_message[message.id] = {"kill_id": kill_id, "group_id": group_id}

        try:
            await message.clear_reaction("⏳")
        except discord.HTTPException:
            pass
        await message.add_reaction("✅")

        party_names   = [m["characterName"] for m in members if m["id"] in party_ids]
        # Show standard drops count (total items registered)
        total_drops   = len(kill_drops)
        assigned      = sum(1 for d in kill_drops if d.get("takenBy"))
        items_summary = f"{assigned}/{total_drops} items assigned"
        if unique_items:
            preview = ", ".join(unique_items[:4])
            if len(unique_items) > 4:
                preview += f" +{len(unique_items) - 4} more"
            items_summary += f" — *{preview}*"
        group_url     = await loop.run_in_executor(None, _get_group_tracker_url, group_id)

        embed = discord.Embed(
            title=f"⚔️ {display_name} kill registered!",
            url=group_url or None,
            color=0x00C853,
            timestamp=message.created_at,
        )
        embed.add_field(name="🗺 Map",    value=f"`{map_id}`",                              inline=True)
        embed.add_field(name="⏱ Timer",  value=f"Next spawn in ≥ **{parent_mvp.get('rMin', '?')} min**", inline=True)
        embed.add_field(name="👥 Party",  value=", ".join(party_names) or "_(not resolved)_", inline=False)
        embed.add_field(name="💎 Drops",  value=items_summary or "_(none detected)_",        inline=False)
        if assigned < total_drops:
            embed.add_field(
                name="⚠️ Unassigned drops",
                value=f"{total_drops - assigned} item(s) have no owner — edit the kill in the tracker to assign them.",
                inline=False,
            )
        embed.set_footer(text=f"Auto-registered from screenshot by {message.author.display_name}  •  confidence {score:.0%}")

        # Mention tag (configurable via /mvpbot mention)
        mention_tag = rc.get("mention", "none")
        mention_str = "" if mention_tag.lower() == "none" else f"{mention_tag} "
        await message.reply(content=mention_str or None, embed=embed)

        # ── Post to weekly forum thread (if configured for this group) ────────
        forum_channel_id = rc.get("forum_channel_id")
        thread_suffix    = rc.get("thread_suffix", "MVP Kills")
        if forum_channel_id:
            try:
                forum_ch = client.get_channel(forum_channel_id)
                if isinstance(forum_ch, discord.ForumChannel):
                    week_thread = await get_or_create_week_thread(
                        forum_ch,
                        thread_suffix,
                        message.created_at,
                    )
                    party_str = ", ".join(party_names) or "_(unresolved)_"
                    log_embed = discord.Embed(
                        title=f"⚔️ {display_name}",
                        color=0x5865F2,
                        timestamp=message.created_at,
                    )
                    log_embed.add_field(name="🗺 Map",   value=f"`{map_id}`",             inline=True)
                    log_embed.add_field(name="👥 Party", value=party_str,                  inline=True)
                    log_embed.add_field(name="💎 Drops", value=f"{assigned}/{total_drops} assigned", inline=False)
                    log_embed.set_footer(text=f"By {message.author.display_name}  •  {score:.0%} confidence")
                    await week_thread.send(embed=log_embed)
            except Exception as log_exc:
                print(f"[bot] Kill-log thread post failed: {log_exc}")

    except Exception as exc:
        await _fail(message, f"Unexpected error: `{exc}`")
        import traceback
        traceback.print_exc()


async def _fail(message: discord.Message, reason: str) -> None:
    try:
        await message.clear_reaction("⏳")
    except discord.HTTPException:
        pass
    await message.add_reaction("❓")
    await message.reply(f"⚠️ **Could not auto-register this kill**\n{reason}")


@client.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    """Update kill party when the original message's @mentions change."""
    if after.author.bot:
        return

    # Only act if mentions actually changed
    before_ids = {u.id for u in before.mentions}
    after_ids  = {u.id for u in after.mentions}
    if before_ids == after_ids:
        return

    kill_info = _kill_by_message.get(after.id)
    if not kill_info:
        return  # Not a message we processed

    group_id = kill_info["group_id"]
    kill_id  = kill_info["kill_id"]

    loop = asyncio.get_event_loop()
    members = await loop.run_in_executor(None, get_members, group_id)
    by_discord_id = {str(m["discordId"]): m for m in members if m.get("discordId")}

    # Sender always stays in party
    sender_member = by_discord_id.get(str(after.author.id))
    sender_id     = sender_member["id"] if sender_member else None

    party_ids: list[str] = []
    for user in after.mentions:
        m = by_discord_id.get(str(user.id))
        if m and m["id"] not in party_ids:
            party_ids.append(m["id"])
    if sender_id and sender_id not in party_ids:
        party_ids.insert(0, sender_id)

    result = await loop.run_in_executor(
        None, update_kill_party, kill_id, group_id, party_ids
    )

    if result.get("ok"):
        try:
            await after.clear_reaction("✅")
        except discord.HTTPException:
            pass
        await after.add_reaction("🔄")
        party_names = [m["characterName"] for m in members if m["id"] in party_ids]
        await after.reply(
            f"🔄 Kill party updated: {', '.join(party_names) or '_(none)_'}",
            mention_author=False,
        )
    else:
        print(f"[bot] Failed to update party for kill {kill_id}: {result}")


if __name__ == "__main__":
    # Pre-warm OCR at startup so the first Discord message doesn't time out
    print("Pre-loading OCR model (downloads ~500 MB on first run, cached after)...")
    _get_reader()
    print("OCR model ready. Connecting to Discord...")
    client.run(DISCORD_TOKEN)
