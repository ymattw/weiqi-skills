#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fox Weiqi share-link SGF downloader
Supports extracting game record SGF from Fox H5 share links
Automatically detects game status: uses the API for finished games and
WebSocket for games in progress

Usage:
    python3 download_share.py <share-link> [output-file]

Examples:
    python3 download_share.py "https://h5.foxwq.com/yehunewshare/?chessid=12345..."
    python3 download_share.py "https://h5.foxwq.com/..." /tmp/game.sgf
"""

import os
import re
import sys
import json
import asyncio
import argparse
import requests
from urllib.parse import parse_qs, urlparse
from datetime import datetime
from contextlib import contextmanager
from collections import OrderedDict

from sgf_parser import parse_sgf as sgf_parse


# Performance timing utility
class PerformanceTimer:
    """Performance timer"""

    def __init__(self):
        self.timings = OrderedDict()
        self.start_time = None

    def start(self):
        self.start_time = datetime.now()
        return self

    @contextmanager
    def step(self, name):
        step_start = datetime.now()
        try:
            yield self
        finally:
            elapsed = (datetime.now() - step_start).total_seconds()
            self.timings[name] = elapsed

    def format_report(self):
        lines = ["\n" + "=" * 50, "⏱️  Performance Timing Report", "=" * 50]
        for name, elapsed in self.timings.items():
            lines.append(f"  {name:25s} : {elapsed:>8.3f}s")
        lines.append("=" * 50)
        return "\n".join(lines)


# Global timer
timer = PerformanceTimer()


def parse_share_url(url):
    """Parse the share link and extract its parameters"""
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    return {
        "roomid": params.get("roomid", [None])[0],
        "gameid": params.get("chessid", [None])[0],
        "uid": params.get("uid", [None])[0],
        "createtime": params.get("createtime", [None])[0],
        "full_url": url,
    }


def extract_via_api(gameid):
    """
    Fetch the historical game record SGF via the API
    Suitable for finished games

    API endpoint: https://h5.foxwq.com/yehuDiamond/chessbook_local/YHWQFetchChess
    """
    api_url = f"https://h5.foxwq.com/yehuDiamond/chessbook_local/YHWQFetchChess?chessid={gameid}"

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
        "Accept": "application/json",
        "Referer": "https://h5.foxwq.com/",
    }

    try:
        response = requests.get(api_url, headers=headers, timeout=15)
        response.raise_for_status()

        data = response.json()

        if data.get("result") != 0:
            print(f"⚠️ API returned error code: {data.get('result')}")
            return None

        sgf = data.get("chess")
        if not sgf:
            print("⚠️ API did not return game record data")
            return None

        return sgf

    except requests.exceptions.RequestException as e:
        print(f"⚠️ API request failed: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"⚠️ Failed to parse API response: {e}")
        return None


def extract_game_info(gameid, uid=None):
    """
    Fetch basic game information

    API endpoint: https://h5.foxwq.com/yehuDiamond/chessbook_local/FetchChessSummaryByChessID
    """
    uid_param = f"&uid={uid}" if uid else ""
    api_url = f"https://h5.foxwq.com/yehuDiamond/chessbook_local/FetchChessSummaryByChessID?with_edu=1&chessid={gameid}{uid_param}"

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
        "Accept": "application/json",
        "Referer": "https://h5.foxwq.com/",
    }

    try:
        response = requests.get(api_url, headers=headers, timeout=10)
        response.raise_for_status()

        data = response.json()

        if data.get("result") != 0:
            return None

        gamelist = data.get("chesslist", {})
        return {
            "black_nick": gamelist.get("blacknick", "Black"),
            "white_nick": gamelist.get("whitenick", "White"),
            "black_dan": gamelist.get("blackdan", 0),
            "white_dan": gamelist.get("whitedan", 0),
            "result": gamelist.get("result", ""),
            "start_time": gamelist.get("gamestarttime", ""),
            "movenum": gamelist.get("movenum", 0),
        }

    except Exception as e:
        return None


def extract_moves_from_binary(data):
    """Extract moves from binary data (08 xx 10 yy pattern) - standard live game record"""
    moves = []
    i = 0
    while i < len(data) - 4:
        if data[i] == 0x08 and data[i + 2] == 0x10:
            x = data[i + 1]
            y = data[i + 3]
            if x < 19 and y < 19:
                moves.append((x, y))
                i += 4
                continue
        i += 1
    return moves


def extract_jueyi_live_from_binary(data):
    """
    Extract the main-branch game record from Jueyi commentary live binary data

    Protocol format:
    - Main branch marker: 10 cb 01
    - Move data: 1a12 08<x>10<y>18<color> ...
    - Jueyi comment: jueyi[comment content]

    Args:
        data: Binary data

    Returns:
        list: List of moves [(x, y), ...]
    """
    moves = []

    # Main branch marker
    main_branch_marker = bytes([0x10, 0xCB, 0x01])

    pos = 0
    while True:
        pos = data.find(main_branch_marker, pos)
        if pos == -1:
            break

        # Marker should be followed by: 1a12 08<x>10<y>18<color>
        start = pos + len(main_branch_marker)
        segment = data[start : start + 20]

        if len(segment) < 8:
            pos += 1
            continue

        # Parse move: \x08<x>\x10<y>\x18<color>
        move_match = re.search(
            rb"\x08([\x00-\x13])\x10([\x00-\x13])\x18([\x01\x02])", segment
        )
        if not move_match:
            pos += 1
            continue

        x = move_match.group(1)[0]
        y = move_match.group(2)[0]

        moves.append((x, y))

        pos += 1

    return moves


def is_jueyi_live_data(data):
    """
    Determine whether the data is Jueyi commentary live data

    Criteria:
    - Contains the string "jueyi"
    - Contains the main branch marker 10 cb 01

    Args:
        data: Binary data

    Returns:
        bool: Whether this is Jueyi live data
    """
    # Check whether the string "jueyi" is present
    if b"jueyi" in data:
        return True

    # Check whether the main branch marker is present
    main_branch_marker = bytes([0x10, 0xCB, 0x01])
    if main_branch_marker in data:
        return True

    return False


def extract_handicap_from_binary(data):
    """Extract the handicap count from binary data

    Fox WebSocket protocol GameRule structure:
    - 08 xx: boardsize (19 = 0x13)
    - 10 xx: playingType
    - 18 xx: handicap (number of handicap stones)
    - 20 xx: komi
    """
    try:
        # Method 1: find the GameRule pattern (08 13 10 01 18 xx)
        # boardsize=19(0x13), playingType=1, handicap=xx
        for i in range(len(data) - 6):
            if (
                data[i] == 0x08
                and data[i + 1] == 0x13  # boardsize = 19
                and data[i + 2] == 0x10
                and data[i + 3] == 0x01  # playingType = 1
                and data[i + 4] == 0x18
            ):  # handicap field
                handicap = data[i + 5]
                if 2 <= handicap <= 9:
                    return handicap

        # Method 2: find the HA[number] text pattern (SGF format)
        text = data.decode("utf-8", errors="ignore")
        ha_match = re.search(r"HA\[(\d+)\]", text)
        if ha_match:
            return int(ha_match.group(1))

        return 0
    except Exception:
        return 0


def extract_player_names(data):
    """Extract player names from binary data"""
    names = []
    try:
        idx = 0
        while idx < len(data) - 3:
            if data[idx] == 0x9A and data[idx + 1] == 0x01:
                str_len = data[idx + 2]
                if 3 <= str_len <= 20 and idx + 3 + str_len <= len(data):
                    try:
                        name = data[idx + 3 : idx + 3 + str_len].decode("utf-8")
                        if name and not name.startswith("http") and len(name) > 1:
                            if name not in ["1.14.205.137", "avatar"]:
                                names.append(name)
                    except:
                        pass
            idx += 1

        if not names:
            text = data.decode("utf-8", errors="ignore")
            matches = re.findall(r"([\w\u4e00-\u9fff]+)\[\d+段\]", text)
            names.extend(matches)

        seen = set()
        unique_names = []
        for name in names:
            if name not in seen:
                seen.add(name)
                unique_names.append(name)

        return unique_names[:2]
    except Exception as e:
        return names


async def extract_via_websocket(url, timeout=15, debug=False):
    """
    Extract the game record via WebSocket (optional feature, only for games in progress)

    Automatically detects the game record type:
    - Jueyi commentary live: uses extract_jueyi_live_from_binary
    - Standard live: uses extract_moves_from_binary

    Note: This feature requires the optional dependency playwright
    For historical games use --mode api, which does not require playwright
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("⚠️  Optional dependency not installed: playwright")
        print("   To extract games in progress, please run:")
        print("   pip3 install playwright && playwright install chromium")
        print()
        print("   💡 Tip: for historical games you can use --mode api, no playwright needed")
        return None, None, 0, None

    moves = []
    player_names = []
    handicap = 0
    raw_data = None
    is_jueyi = False

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)"
        )
        page = await context.new_page()

        def handle_ws(ws):
            async def on_message(data):
                nonlocal moves, player_names, handicap, raw_data, is_jueyi
                if isinstance(data, bytes):
                    if len(data) > 1000:
                        raw_data = data

                        # Determine the game record type
                        if not moves:
                            if is_jueyi_live_data(data):
                                is_jueyi = True
                                print("   🎯 Detected Jueyi commentary live game record")
                                moves = extract_jueyi_live_from_binary(data)
                            else:
                                print("   📺 Detected standard live game record")
                                moves = extract_moves_from_binary(data)

                        if not player_names:
                            player_names = extract_player_names(data)
                        if handicap == 0:
                            handicap = extract_handicap_from_binary(data)

            ws.on("framereceived", lambda d: asyncio.create_task(on_message(d)))

        page.on("websocket", handle_ws)

        with timer.step("WebSocket connect and data fetch"):
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(timeout)

        await browser.close()

    # Debug mode: save raw data for analysis
    if debug and raw_data:
        debug_file = f"/tmp/foxwq_ws_debug_{datetime.now().strftime('%H%M%S')}.bin"
        with open(debug_file, "wb") as f:
            f.write(raw_data)
        print(f"   Debug data saved: {debug_file}")

        # Output the first 200 bytes in hex for analysis
        print(f"   First 200 bytes of raw data:")
        hex_str = " ".join(f"{b:02x}" for b in raw_data[:200])
        print(f"   {hex_str}")

        # Try to decode the text portion
        text = raw_data.decode("utf-8", errors="ignore")
        if text:
            print(f"   Decodable text fragments:")
            for line in text.split("\x00")[:10]:
                if len(line) > 3 and len(line) < 100:
                    print(f"     {line}")

    return moves, player_names, handicap, is_jueyi


def create_sgf(moves, pb="Black", pw="White", handicap=0):
    """Create an SGF-format game record

    Handicap game rules:
    - Black places the handicap stones first (marked with AB)
    - White plays the first move
    """
    if not moves:
        return None

    coord_map = "abcdefghijklmnopqrs"
    sgf = f"(;GM[1]FF[4]CA[UTF-8]SZ[19]\n"
    sgf += f"PB[{pb}]PW[{pw}]\n"

    # Add handicap information
    if handicap >= 2:
        sgf += f"HA[{handicap}]\n"
        # Add handicap stones (standard star points)
        handicap_coords = {
            2: [(3, 3), (15, 15)],  # 4-4 diagonal
            3: [(3, 3), (15, 15), (3, 15)],  # 4-4 + 4-16
            4: [(3, 3), (15, 15), (3, 15), (15, 3)],  # 4-4 four corners
            5: [(3, 3), (15, 15), (3, 15), (15, 3), (9, 9)],  # 4-4 + tengen
            6: [(3, 3), (15, 15), (3, 15), (15, 3), (9, 3), (9, 15)],  # 4-4 + side stars
            7: [
                (3, 3),
                (15, 15),
                (3, 15),
                (15, 3),
                (9, 3),
                (9, 15),
                (9, 9),
            ],  # 6 stones + tengen
            8: [
                (3, 3),
                (15, 15),
                (3, 15),
                (15, 3),
                (9, 3),
                (9, 15),
                (3, 9),
                (15, 9),
            ],  # 4-4 + side stars
            9: [
                (3, 3),
                (15, 15),
                (3, 15),
                (15, 3),
                (9, 3),
                (9, 15),
                (3, 9),
                (15, 9),
                (9, 9),
            ],  # nine star points
        }
        if handicap in handicap_coords:
            for hx, hy in handicap_coords[handicap]:
                sgf += f";AB[{coord_map[hx]}{coord_map[hy]}]\n"

    # Handle move order for handicap games
    # With handicap: White plays first (since Black already placed the handicap stones)
    # Without handicap: Black plays first
    for i, (x, y) in enumerate(moves):
        if handicap >= 2:
            # Handicap game: White plays first
            color = "W" if i % 2 == 0 else "B"
        else:
            # Normal game: Black plays first
            color = "B" if i % 2 == 0 else "W"
        if 0 <= x < 19 and 0 <= y < 19:
            sgf += f";{color}[{coord_map[x]}{coord_map[y]}]\n"

    sgf += ")"
    return sgf


def parse_sgf_info(sgf):
    """Extract information from the SGF (parsed using sgf_parser)"""
    result = sgf_parse(sgf)
    game_info = result.get("game_info", {})
    stats = result.get("stats", {})

    info = {
        "pb": game_info.get("black", "Black"),
        "pw": game_info.get("white", "White"),
        "br": game_info.get("black_rank", ""),
        "wr": game_info.get("white_rank", ""),
        "result": game_info.get("result", ""),
        "date": game_info.get("date", ""),
        "movenum": stats.get("move_nodes", 0),
    }

    return info


def extract_from_share_link(url, output_path=None, mode="auto"):
    """
    Main function: extract SGF from a share link

    Args:
        url: Share link
        output_path: Output file path (optional)
        mode: Extraction mode ('auto', 'api', 'websocket')
              auto - choose automatically (prefer API)
              api - use the API only
              websocket - use WebSocket (auto-detects standard/Jueyi live)
    """

    print("=" * 60)
    print("🎯 Fox Weiqi Share-Link SGF Downloader")
    print("=" * 60)

    timer.start()

    # Parse the URL
    with timer.step("Parse share link"):
        params = parse_share_url(url)

    if not params["gameid"]:
        print("❌ Invalid share link, could not extract gameid")
        return None

    print(f"\nGame information:")
    print(f"  Game ID: {params['gameid']}")
    print(f"  Extraction mode: {mode}")
    print()

    sgf = None
    game_info = None

    # Choose the extraction method based on the mode
    if mode in ("auto", "api"):
        print("🔍 Trying to fetch the game record via API...")
        with timer.step("Fetch game record via API"):
            sgf = extract_via_api(params["gameid"])

        if sgf:
            print("✅ API fetch succeeded!")
            # Also fetch game information
            game_info = extract_game_info(params["gameid"], params.get("uid"))
        elif mode == "api":
            print("❌ API fetch failed")
            return None

    # If the API failed and this is not API-only mode, try WebSocket
    if not sgf and mode in ("auto", "websocket"):
        print("🌐 Trying to fetch the game record via WebSocket...")
        print("   (suitable for games in progress)")

        moves, player_names, handicap, is_jueyi = asyncio.run(
            extract_via_websocket(url)
        )

        if moves:
            if is_jueyi:
                print(f"✅ Jueyi live game record fetched successfully! {len(moves)} moves total")
            else:
                print(f"✅ Standard live game record fetched successfully! {len(moves)} moves total")

            pb = player_names[0] if len(player_names) > 0 else "Black"
            pw = player_names[1] if len(player_names) > 1 else "White"

            if handicap > 0:
                print(f"   Detected handicap: {handicap} stones")

            with timer.step("Generate SGF"):
                sgf = create_sgf(moves, pb, pw, handicap)

            game_info = {
                "pb": pb,
                "pw": pw,
                "movenum": len(moves),
                "handicap": handicap,
            }
        else:
            print("❌ WebSocket fetch failed")

    if not sgf:
        print("\n❌ Could not extract game record data")
        print("   Possible reasons:")
        print("   - The game has ended and was not saved")
        print("   - The share link has expired")
        print("   - Login permission is required")
        print(timer.format_report())
        return None

    # Parse SGF information
    sgf_info = parse_sgf_info(sgf)

    # Merge information (API information takes priority)
    if game_info:
        sgf_info.update({k: v for k, v in game_info.items() if v})

    print(f"\n📋 Game details:")
    print(f"  Black: {sgf_info['pb']} {sgf_info['br']}")
    print(f"  White: {sgf_info['pw']} {sgf_info['wr']}")
    print(f"  Result: {sgf_info['result']}")
    print(f"  Date: {sgf_info['date']}")
    print(f"  Moves: {sgf_info['movenum']}")

    # Determine the output path
    if not output_path:
        output_path = f"/tmp/foxwq_{params['gameid']}.sgf"

    # Save the file
    with timer.step("Save file"):
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(sgf)

    print(f"\n💾 SGF saved: {output_path}")

    # Show the first 10 moves
    moves = re.findall(r";([BW])\[([a-z]{2})\]", sgf)
    if moves:
        print(f"\nFirst 10 moves preview:")
        for i, (color, coord) in enumerate(moves[:10]):
            x = ord(coord[0]) - ord("a")
            y = ord(coord[1]) - ord("a")
            color_zh = "B" if color == "B" else "W"
            coord_str = chr(ord("A") + x) + str(19 - y)
            print(f"  {i+1}. {color_zh}: {coord_str}")

    print(timer.format_report())

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Download SGF game records from Fox Weiqi share links",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 download_share.py "https://h5.foxwq.com/yehunewshare/?chessid=123..."
  python3 download_share.py "https://h5.foxwq.com/..." /tmp/game.sgf
  python3 download_share.py "..." --mode api        # Use API only
  python3 download_share.py "..." --mode websocket  # Use WebSocket (auto-detects record type)
        """,
    )

    parser.add_argument("url", help="Fox H5 share link")
    parser.add_argument("output", nargs="?", help="Output SGF file path (optional)")
    parser.add_argument(
        "--mode",
        choices=["auto", "api", "websocket"],
        default="auto",
        help="Extraction mode (default: auto)",
    )

    args = parser.parse_args()

    result = extract_from_share_link(args.url, args.output, args.mode)

    if result:
        print(f"\n✅ Download succeeded: {result}")
        sys.exit(0)
    else:
        print("\n❌ Download failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
