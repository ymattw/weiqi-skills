#!/usr/bin/env python3
"""
Fox Weiqi - Download game records by nickname
Supports: nickname to UID lookup -> fetch game list -> download SGF

Usage:
    python3 download_by_name.py <nickname> [--limit N] [--output-dir DIR]
    python3 download_by_name.py KataGo
    python3 download_by_name.py KataGo --limit 10 --output-dir /tmp/qipu

Note: This script retrieves data through the platform's public API and is
intended for personal study and research only.
"""

import argparse
import sys
import os
import json
import re
import urllib.request
import urllib.parse
import time

# API configuration (source: open-source project GetFoxRequest.java)
QUERY_USER_URL = "https://newframe.foxwq.com/cgi/QueryUserInfoPanel"
CHESS_LIST_URL = "https://h5.foxwq.com/yehuDiamond/chessbook_local/YHWQFetchChessList"
FETCH_CHESS_URL = "https://h5.foxwq.com/yehuDiamond/chessbook_local/YHWQFetchChess"
MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"
)


def http_get(url, timeout=20):
    """Send an HTTP GET request"""
    req = urllib.request.Request(url)
    req.add_header("User-Agent", MOBILE_USER_AGENT)
    req.add_header("Accept", "application/json,text/plain,*/*")

    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8")


def query_user_by_name(nickname):
    """
    Look up user information by nickname

    Calls the platform's user query endpoint to obtain basic information
    such as the UID based on the nickname.
    A valid API endpoint is required for this to work.
    """
    encoded_name = urllib.parse.quote(nickname)
    # Build request URL: srcuid=0 means querying as a guest
    url = f"{QUERY_USER_URL}?srcuid=0&username={encoded_name}"

    response = http_get(url)
    data = json.loads(response)

    if data.get("result") != 0:
        error_msg = data.get("resultstr") or data.get("errmsg") or "unknown error"
        raise Exception(f"Failed to query user: {error_msg}")

    uid = str(data.get("uid", "")).strip()
    if not uid:
        raise Exception("No UID found for this nickname")

    return {
        "uid": uid,
        "nickname": data.get("username")
        or data.get("name")
        or data.get("englishname")
        or nickname,
        "dan": data.get("dan", 0),
        "total_win": data.get("totalwin", 0),
        "total_lost": data.get("totallost", 0),
        "total_equal": data.get("totalequal", 0),
    }


def fetch_chess_list(uid, lastcode="0"):
    """
    Fetch the game record list

    Calls the platform's game list endpoint to retrieve the specified
    user's public game records.
    type=1 is the query type, and lastcode is used for pagination.
    """
    encoded_uid = urllib.parse.quote(uid)
    # Build request URL: type=1 means fetching the game list
    url = f"{CHESS_LIST_URL}?srcuid=0&dstuid={encoded_uid}&type=1&lastcode={lastcode}&searchkey=&uin={encoded_uid}"

    response = http_get(url)
    data = json.loads(response)

    if data.get("result") != 0:
        error_msg = data.get("resultstr") or "Failed to fetch game list"
        raise Exception(error_msg)

    return data.get("chesslist", [])


def fetch_sgf(chessid):
    """
    Download a single game as SGF

    Retrieves the SGF-format game data for the given game ID.
    """
    url = f"{FETCH_CHESS_URL}?chessid={chessid}"

    response = http_get(url)
    data = json.loads(response)

    if data.get("result") != 0:
        raise Exception(f"Failed to download game record: {data.get('resultstr', 'unknown error')}")

    return data.get("chess", "")


def format_dan(dan_value):
    """Format the rank for display"""
    if dan_value >= 100:
        return f"Pro {dan_value - 100}d"
    elif dan_value >= 24:
        return f"{dan_value - 20}d"
    elif dan_value >= 20:
        return f"{dan_value - 20}d"
    elif dan_value >= 10:
        return f"{dan_value - 10}k"
    else:
        return f"{dan_value}k"


def parse_result(winner, point, reason):
    """
    Parse the game result

    Parameters:
    - winner: 1=Black wins, 2=White wins, 0=Draw
    - point: winning margin in stones (valid for scoring wins)
    - reason: 1=scoring win, 2=timeout, 3=middle-game win, 4=resignation
    """
    if winner == 0:
        return "Draw"

    winner_str = "Black wins" if winner == 1 else "White wins"

    if reason == 1:
        if point > 0:
            return f"{winner_str} by {point}"
        return winner_str
    elif reason == 2:
        return f"{winner_str} (timeout)"
    elif reason == 3:
        return f"{winner_str} (middle game)"
    elif reason == 4:
        return f"{winner_str} (resignation)"
    else:
        return winner_str


# Utility functions


def main():
    parser = argparse.ArgumentParser(
        epilog=(
            "Examples:\n"
            "  python3 download_by_name.py KataGo\n"
            "  python3 download_by_name.py KataGo --limit 5 --output-dir /tmp/qipu"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("nickname", help="User nickname")
    parser.add_argument(
        "-l", "--limit", type=int, default=1, help="Download limit (default 1)"
    )
    parser.add_argument(
        "-o", "--output-dir", default=".", help="Output directory (default current directory)"
    )
    args = parser.parse_args()

    nickname = args.nickname
    limit = args.limit
    output_dir = args.output_dir

    print("=" * 60)
    print("🎯 Fox Weiqi - Download game records by nickname")
    print("=" * 60)
    print()

    start_time = time.time()

    # 1. Query user information
    print(f"🔍 Looking up nickname: {nickname} ...")
    try:
        user_info = query_user_by_name(nickname)
    except Exception as e:
        print(f"❌ Lookup failed: {e}")
        sys.exit(1)

    uid = user_info["uid"]
    print(f"✅ Found user {nickname}!")
    print(f"   UID: {uid}")
    print(f"   Nickname: {user_info['nickname']}")
    print(f"   Rank: {format_dan(user_info['dan'])}")
    print(
        f"   Record: {user_info['total_win']}W {user_info['total_lost']}L {user_info['total_equal']}D"
    )
    print()

    # 2. Fetch the game list
    print("📋 Fetching game list...")
    try:
        chess_list = fetch_chess_list(uid)
    except Exception as e:
        print(f"❌ Failed to fetch game list: {e}")
        sys.exit(1)

    if not chess_list:
        print("⚠️ This user has no public game records")
        sys.exit(0)

    total_games = len(chess_list)
    print(f"✅ Found {total_games} game records")
    print()

    # 3. Show the game list
    print("=" * 60)
    print("📊 Game list (most recent {})".format(limit if limit else total_games))
    print("=" * 60)
    print()

    games_to_show = chess_list[:limit] if limit else chess_list

    for idx, game in enumerate(games_to_show, 1):
        chessid = game.get("chessid", "")
        black_nick = game.get("blacknick", "Black")
        white_nick = game.get("whitenick", "White")
        black_dan = format_dan(game.get("blackdan", 0))
        white_dan = format_dan(game.get("whitedan", 0))
        start_time_str = game.get("starttime", "unknown")
        movenum = game.get("movenum", 0)
        winner = game.get("winner", 0)
        point = game.get("point", 0)
        reason = game.get("reason", 0)
        result = parse_result(winner, point, reason)

        print(
            f"{idx}. [{start_time_str}] {black_nick}({black_dan}) vs {white_nick}({white_dan})"
        )
        print(f"   Result: {result} | Moves: {movenum} | ID: {chessid}")
        print()

    # 4. Download game records
    print()
    print("=" * 60)
    print("⬇️  Starting download...")
    print("=" * 60)
    print()

    os.makedirs(output_dir, exist_ok=True)
    success_count = 0
    failed_list = []

    for idx, game in enumerate(games_to_show, 1):
        chessid = game.get("chessid", "")
        start_time_str = (
            game.get("starttime", "unknown").replace(" ", "_").replace(":", "-")
        )

        # Generate the file name
        safe_nickname = re.sub(r"[^\w\u4e00-\u9fff]", "_", nickname)
        filename = f"{idx:03d}_{safe_nickname}_{start_time_str}_{chessid}.sgf"
        filepath = os.path.join(output_dir, filename)

        print(f"[{idx}/{len(games_to_show)}] Downloading {chessid} ...", end=" ")

        try:
            sgf_content = fetch_sgf(chessid)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(sgf_content)
            print(f"✅ Saved: {filename}")
            success_count += 1
            time.sleep(0.2)  # Avoid making requests too quickly
        except Exception as e:
            print(f"❌ Failed: {e}")
            failed_list.append((chessid, str(e)))

    # 5. Report
    elapsed = time.time() - start_time
    print()
    print("=" * 60)
    print("📈 Download Report")
    print("=" * 60)
    print(f"   User: {nickname} (UID: {uid})")
    print(f"   Save directory: {output_dir}")
    print(f"   Success: {success_count}/{len(games_to_show)}")
    print(f"   Elapsed: {elapsed:.2f}s")

    if failed_list:
        print()
        print("❌ Failed downloads:")
        for chessid, error in failed_list:
            print(f"   - {chessid}: {error}")

    print()
    print("✅ Done!")


if __name__ == "__main__":
    main()
