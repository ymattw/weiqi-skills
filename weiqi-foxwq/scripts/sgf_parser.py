#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SGF tree parsing module - full support for nested variation branches

Only two public interfaces:
- parse_sgf(sgf_content: str) -> dict
- parse_sgf_file(filepath: str) -> dict

Fixed tree node structure:
{
    "properties": dict,
    "is_root": bool,
    "move_number": int,
    "color": str|None,
    "coord": str|None,
    "children": list
}
"""

import unittest
from typing import Optional, List, Dict, Any, Tuple


def coord_to_pos(coord: str) -> Optional[Tuple[int, int]]:
    """Convert an SGF coordinate (such as 'pd') to numeric coordinates (x, y)"""
    if not coord or len(coord) < 2:
        return None
    x = ord(coord[0]) - 97
    y = ord(coord[1]) - 97
    return (x, y)


def pos_to_coord(x: int, y: int) -> str:
    """Convert numeric coordinates to an SGF coordinate"""
    return chr(97 + x) + chr(97 + y)


def parse_sgf(sgf_content: str) -> dict:
    """
    Parse SGF content

    Return structure:
    {
        "game_info": {
            "board_size": 19,
            "black": "Black",
            "white": "White",
            "black_rank": "9d",
            "white_rank": "9d",
            "game_name": "Go game record",
            "date": "2024-01-01",
            "result": "B+R",
            "komi": "375",
            "handicap": 0,
            "handicap_stones": [{"x": 15, "y": 3, "color": "B"}, ...]
        },
        "tree": {
            "properties": {"GM": "1", "FF": "4", ...},
            "is_root": true,
            "move_number": 0,
            "color": null,
            "coord": null,
            "children": [...]
        },
        "stats": {
            "total_nodes": 150,
            "move_nodes": 149,
            "max_depth": 80,
            "branch_count": 3
        },
        "errors": []
    }
    """
    parser = _SGFParser()
    return parser.parse(sgf_content)


def parse_sgf_file(filepath: str) -> dict:
    """Parse an SGF file"""
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    return parse_sgf(content)


class _SGFParser:
    """Internal parser implementation"""

    def __init__(self):
        self.errors: List[str] = []
        self._pending_branch_props: Dict[str, Any] = {}  # Cache properties at the start of a branch

    def parse(self, sgf_content: str) -> dict:
        """Parse SGF content"""
        self.errors = []

        content = sgf_content.strip()
        if not content:
            self.errors.append("SGF content is empty")
            return self._create_empty_result()

        try:
            root_node = self._parse_tree(content)
            tree = self._node_to_dict(root_node)
            stats = self._calc_stats(tree)
            game_info = self._extract_game_info(tree)

            # Extract main-branch moves
            moves = self._extract_main_moves(tree)

            # Extract variations
            variations = self._extract_variations(tree)

            return {
                "game_info": game_info,
                "tree": tree,
                "stats": stats,
                "moves": moves,
                "variations": variations,
                "errors": self.errors,
            }
        except Exception as e:
            self.errors.append(f"Parse error: {str(e)}")
            return self._create_empty_result()

    def _create_empty_result(self) -> dict:
        """Create an empty result"""
        empty_tree = {
            "properties": {},
            "is_root": True,
            "move_number": 0,
            "color": None,
            "coord": None,
            "children": [],
        }
        return {
            "game_info": self._extract_game_info(empty_tree),
            "tree": empty_tree,
            "stats": {
                "total_nodes": 1,
                "move_nodes": 0,
                "max_depth": 0,
                "branch_count": 0,
            },
            "moves": [],
            "variations": {},
            "errors": self.errors,
        }

    def _parse_tree(self, content: str) -> "_SGFNode":
        """Parse the tree structure

        SGF format: (;GM[1](;B[pd];W[pp])(;B[dd]))
        - A sequence itself does not create a node; the first ';' inside a sequence
          determines the parent node
        - '(' only marks entering a new level, ')' marks leaving
        """
        # Parent stack: stores the sequence's parent (the parent of the first node in the sequence)
        parent_stack: List["_SGFNode"] = []
        # The node currently processed within the sequence
        seq_current: Optional["_SGFNode"] = None
        root: Optional["_SGFNode"] = None
        i = 0
        n = len(content)
        paren_count = 0

        while i < n:
            char = content[i]

            if char == "(":
                # Start a new sequence
                if seq_current is not None:
                    # There is a current node in the sequence; use it as the new sequence's parent
                    parent_stack.append(seq_current)
                elif parent_stack:
                    # No node in the sequence but a parent stack exists; duplicate the top
                    parent_stack.append(parent_stack[-1])
                elif root is not None:
                    # No parent stack but a root exists; the root is the new sequence's parent
                    parent_stack.append(root)
                # else: first sequence, parent_stack stays empty

                paren_count += 1
                seq_current = None
                i += 1

                # Look ahead and cache properties after '(' (e.g. C[...]) until ';' or ')'
                self._pending_branch_props = {}
                while i < n:
                    c = content[i]
                    if c in "();":
                        break
                    if c in " \t\n\r":
                        i += 1
                        continue
                    if c.isupper():
                        prop_name = ""
                        while i < n and content[i].isupper():
                            prop_name += content[i]
                            i += 1
                        values = []
                        while i < n and content[i] == "[":
                            value, i, closed = self._parse_property_value(
                                content, i + 1
                            )
                            if not closed:
                                self.errors.append(f"Property {prop_name} value not closed")
                            values.append(value)
                        if values:
                            self._pending_branch_props[prop_name] = (
                                values if len(values) > 1 else values[0]
                            )
                    else:
                        self.errors.append(f"Position {i}: unexpected character '{c}' in branch comment, skipping")
                        i += 1

            elif char == ")":
                # End the current sequence
                if paren_count > 0:
                    if parent_stack:
                        parent = parent_stack.pop()
                        # After the sequence ends, seq_current should be the sequence's parent
                        # so the next sequence attaches to the same parent correctly
                        seq_current = parent
                    else:
                        seq_current = None
                    paren_count -= 1
                else:
                    self.errors.append(f"Position {i}: extra closing parenthesis")
                i += 1

            elif char == ";":
                # Create a new node
                new_node = _SGFNode()

                # Determine the parent node
                if seq_current is not None and not seq_current.properties:
                    # seq_current is the empty node just created by '('; give it properties
                    # This should not happen since we no longer create nodes on '('
                    parent = seq_current
                elif seq_current is not None:
                    # A node already exists in the sequence; the new node becomes its child (main-branch continuation)
                    seq_current.children.append(new_node)
                    new_node.parent = seq_current
                    new_node.move_number = (
                        seq_current.move_number + 1 if not seq_current.is_root else 1
                    )
                elif parent_stack:
                    # First node of the sequence; parent is the top of parent_stack
                    parent = parent_stack[-1]
                    parent.children.append(new_node)
                    new_node.parent = parent
                    new_node.move_number = (
                        parent.move_number + 1 if not parent.is_root else 1
                    )
                elif root is None:
                    # First node, treated as the root
                    root = new_node
                    new_node.is_root = True
                    new_node.move_number = 0
                else:
                    # Another top-level node (case without parentheses)
                    if root.is_root and len(root.children) == 0 and not root.properties:
                        # The root is empty; use it directly
                        root.properties = {}
                        new_node.parent = root
                        new_node.move_number = 1
                        root.children.append(new_node)
                    elif root.is_root:
                        # Create a wrapper node
                        wrapper = _SGFNode()
                        wrapper.is_root = True
                        wrapper.move_number = 0

                        if not root.properties and len(root.children) == 0:
                            # root is empty; replace it
                            root = wrapper
                        else:
                            # Move the original root
                            root.parent = wrapper
                            root.move_number = 1
                            wrapper.children.append(root)
                            root = wrapper

                        new_node.parent = root
                        new_node.move_number = 1
                        root.children.append(new_node)
                    else:
                        new_node.parent = root
                        new_node.move_number = 1
                        root.children.append(new_node)

                # Parse properties
                props, i = self._parse_properties(content, i + 1)

                # Merge cached branch properties (if any)
                if self._pending_branch_props:
                    # Cached properties take priority, but already-parsed properties are not overwritten
                    merged_props = self._pending_branch_props.copy()
                    merged_props.update(props)
                    props = merged_props
                    self._pending_branch_props = {}  # Clear the cache

                new_node.properties = props
                self._extract_move_info(new_node)

                seq_current = new_node

            elif char in " \t\n\r":
                i += 1
            else:
                self.errors.append(f"Position {i}: unexpected character '{char}', skipping")
                i += 1

        if paren_count > 0:
            self.errors.append("Warning: parentheses not fully closed")

        return root or _SGFNode()

    def _parse_properties(self, content: str, start: int) -> tuple:
        """Parse a property list; returns (property dict, new position)"""
        props: Dict[str, Any] = {}
        i = start
        n = len(content)

        while i < n:
            char = content[i]

            if char in "();":
                break

            if char in " \t\n\r":
                i += 1
                continue

            if char.isupper():
                prop_name = ""
                while i < n and content[i].isupper():
                    prop_name += content[i]
                    i += 1

                values = []
                while i < n and content[i] == "[":
                    value, i, closed = self._parse_property_value(content, i + 1)
                    if not closed:
                        self.errors.append(f"Property {prop_name} value not closed")
                    values.append(value)

                if values:
                    props[prop_name] = values if len(values) > 1 else values[0]
                else:
                    props[prop_name] = ""
            else:
                self.errors.append(f"Position {i}: property name must be uppercase, skipping '{char}'")
                i += 1

        return props, i

    def _parse_property_value(self, content: str, start: int) -> tuple:
        """Parse a property value, handling escapes correctly
        Returns: (value, new position, whether properly closed)
        """
        value = []
        i = start
        n = len(content)

        while i < n:
            char = content[i]

            if char == "\\" and i + 1 < n:
                next_char = content[i + 1]
                # SGF escape rules: \] -> ], \\ -> \, \n -> newline, etc.
                if next_char == "]":
                    value.append("]")
                    i += 2
                elif next_char == "\\":
                    value.append("\\")
                    i += 2
                elif next_char == "n":
                    value.append("\n")
                    i += 2
                elif next_char == "r":
                    value.append("\r")
                    i += 2
                elif next_char == "t":
                    value.append("\t")
                    i += 2
                else:
                    # Keep other characters as-is
                    value.append(next_char)
                    i += 2
            elif char == "]":
                # Found the closing bracket
                i += 1
                return "".join(value), i, True
            else:
                value.append(char)
                i += 1

        # Not properly closed
        return "".join(value), i, False

    def _extract_move_info(self, node: "_SGFNode"):
        """Extract color and coord from the properties"""
        if "B" in node.properties:
            node.color = "B"
            node.coord = self._normalize_coord(node.properties["B"])
        elif "W" in node.properties:
            node.color = "W"
            node.coord = self._normalize_coord(node.properties["W"])

    def _normalize_coord(self, val: Any) -> Optional[str]:
        """Normalize the coordinate format"""
        if isinstance(val, list) and val:
            return val[0] if val[0] else None
        return val if val else None

    def _node_to_dict(self, node: "_SGFNode") -> dict:
        """Convert a node to a dictionary"""
        return {
            "properties": node.properties,
            "is_root": node.is_root,
            "move_number": node.move_number,
            "color": node.color,
            "coord": node.coord,
            "children": [self._node_to_dict(c) for c in node.children],
        }

    def _calc_stats(self, tree: dict) -> dict:
        """Compute statistics"""
        total_nodes = 0
        move_nodes = 0
        max_depth = 0
        branch_count = 0

        def traverse(node: dict):
            nonlocal total_nodes, move_nodes, max_depth, branch_count
            total_nodes += 1
            if not node.get("is_root", False):
                move_nodes += 1
            max_depth = max(max_depth, node.get("move_number", 0))
            children = node.get("children", [])
            # More than 1 child means a branch (all but the first main branch are variations)
            if len(children) > 1:
                branch_count += len(children) - 1
            for child in children:
                traverse(child)

        traverse(tree)

        return {
            "total_nodes": total_nodes,
            "move_nodes": move_nodes,
            "max_depth": max_depth,
            "branch_count": branch_count,
        }

    def _extract_game_info(self, tree: dict) -> dict:
        """Extract game information from the root node"""
        props = tree.get("properties", {})

        # Get the first child's properties (preset stones may be here)
        children = tree.get("children", [])
        child_props = children[0].get("properties", {}) if children else {}

        def get_prop(key: str, default: str = "") -> str:
            val = props.get(key, default)
            if isinstance(val, list) and val:
                return str(val[0])
            return str(val) if val else default

        # Board size
        try:
            board_size = int(get_prop("SZ", "19"))
        except ValueError:
            board_size = 19

        # Handicap count
        try:
            handicap = int(get_prop("HA", "0"))
        except ValueError:
            handicap = 0

        # Handicap stone positions
        handicap_stones = []

        # Parse AB[] (added black stones) - check the root, then the first child
        ab_prop = props.get("AB", child_props.get("AB", []))
        if isinstance(ab_prop, str):
            ab_prop = [ab_prop]
        if isinstance(ab_prop, list):
            for coord in ab_prop:
                if coord and len(str(coord)) >= 2:
                    c = str(coord)
                    x = ord(c[0]) - 97
                    y = ord(c[1]) - 97
                    if 0 <= x < board_size and 0 <= y < board_size:
                        handicap_stones.append({"x": x, "y": y, "color": "B"})

        # Parse AW[] (added white stones) - check the root, then the first child
        aw_prop = props.get("AW", child_props.get("AW", []))
        if isinstance(aw_prop, str):
            aw_prop = [aw_prop]
        if isinstance(aw_prop, list):
            for coord in aw_prop:
                if coord and len(str(coord)) >= 2:
                    c = str(coord)
                    x = ord(c[0]) - 97
                    y = ord(c[1]) - 97
                    if 0 <= x < board_size and 0 <= y < board_size:
                        handicap_stones.append({"x": x, "y": y, "color": "W"})

        return {
            "board_size": board_size,
            "black": get_prop("PB", "Black"),
            "white": get_prop("PW", "White"),
            "black_rank": get_prop("BR"),
            "white_rank": get_prop("WR"),
            "event": get_prop("EV"),
            "game_name": get_prop("GN", "Go game record"),
            "date": get_prop("DT"),
            "result": get_prop("RE"),
            "komi": get_prop("KM", "375"),
            "handicap": handicap,
            "handicap_stones": handicap_stones,
        }

    def _extract_main_moves(self, tree: dict) -> List[Dict[str, str]]:
        """Extract the main-branch moves from the tree"""
        moves = []
        node = tree
        while node.get("children"):
            node = node["children"][0]
            color = node.get("color")
            coord = node.get("coord")
            if color and coord:
                moves.append({"color": color, "coord": coord})
        return moves

    def _extract_variations(self, tree: dict) -> Dict[int, List[dict]]:
        """Extract variations from the tree"""
        variations = {}

        def traverse(node, move_num):
            if not node.get("children"):
                return

            for i, child in enumerate(node["children"]):
                # Collect this branch's moves
                child_moves = []
                current = child
                while current:
                    color = current.get("color")
                    coord = current.get("coord")
                    if color and coord:
                        child_moves.append({"color": color, "coord": coord})

                    if current.get("children"):
                        current = current["children"][0]
                    else:
                        break

                # i > 0 means this is a variation branch
                if i > 0 and child_moves:
                    if move_num not in variations:
                        variations[move_num] = []

                    # Extract the comment
                    comment = ""
                    props = child.get("properties", {})
                    if "C" in props:
                        c = props["C"]
                        if isinstance(c, list) and c:
                            comment = c[0]
                        else:
                            comment = str(c) if c else ""

                    name = f"Variation {len(variations[move_num]) + 1}"

                    variations[move_num].append(
                        {"name": name, "moves": child_moves, "comment": comment}
                    )

                next_move_num = move_num
                if child.get("color") and child.get("coord"):
                    next_move_num = move_num + 1

                traverse(child, next_move_num)

        traverse(tree, 0)
        return variations


class _SGFNode:
    """Internal node class"""

    def __init__(self):
        self.properties: Dict[str, Any] = {}
        self.is_root: bool = False
        self.move_number: int = 0
        self.color: Optional[str] = None
        self.coord: Optional[str] = None
        self.parent: Optional["_SGFNode"] = None
        self.children: List["_SGFNode"] = []


# ============ Unit tests ============


class TestSGFParser(unittest.TestCase):
    """SGF parser unit tests"""

    def test_empty_sgf(self):
        """Test an empty SGF"""
        result = parse_sgf("")
        self.assertIn("SGF content is empty", result["errors"])
        self.assertEqual(result["stats"]["total_nodes"], 1)
        self.assertEqual(result["stats"]["move_nodes"], 0)

    def test_root_only(self):
        """Test a root-only document"""
        sgf = "(;GM[1]FF[4]PB[Black]PW[White])"
        result = parse_sgf(sgf)

        self.assertEqual(result["game_info"]["black"], "Black")
        self.assertEqual(result["game_info"]["white"], "White")
        self.assertEqual(result["tree"]["is_root"], True)
        self.assertEqual(result["tree"]["move_number"], 0)
        self.assertEqual(result["tree"]["color"], None)
        self.assertEqual(result["tree"]["coord"], None)
        self.assertEqual(len(result["tree"]["children"]), 0)
        self.assertEqual(result["stats"]["total_nodes"], 1)
        self.assertEqual(result["stats"]["move_nodes"], 0)
        self.assertEqual(len(result["errors"]), 0)

    def test_single_branch(self):
        """Test a single branch (standard game record)"""
        sgf = "(;GM[1];B[pd];W[pp];B[dd])"
        result = parse_sgf(sgf)

        self.assertEqual(result["stats"]["total_nodes"], 4)
        self.assertEqual(result["stats"]["move_nodes"], 3)
        self.assertEqual(result["stats"]["max_depth"], 3)
        self.assertEqual(result["stats"]["branch_count"], 0)

        tree = result["tree"]
        self.assertEqual(len(tree["children"]), 1)

        first_move = tree["children"][0]
        self.assertEqual(first_move["is_root"], False)
        self.assertEqual(first_move["move_number"], 1)
        self.assertEqual(first_move["color"], "B")
        self.assertEqual(first_move["coord"], "pd")
        self.assertEqual(first_move["properties"]["B"], "pd")

        second_move = first_move["children"][0]
        self.assertEqual(second_move["move_number"], 2)
        self.assertEqual(second_move["color"], "W")
        self.assertEqual(second_move["coord"], "pp")

    def test_root_variations(self):
        """Test multiple branches at the root (no main branch)"""
        sgf = "(;GM[1](;B[pd])(;B[dd]))"
        result = parse_sgf(sgf)

        self.assertEqual(result["stats"]["total_nodes"], 3)
        self.assertEqual(result["stats"]["move_nodes"], 2)
        self.assertEqual(result["stats"]["branch_count"], 1)

        tree = result["tree"]
        self.assertEqual(len(tree["children"]), 2)

        # First branch
        self.assertEqual(tree["children"][0]["color"], "B")
        self.assertEqual(tree["children"][0]["coord"], "pd")

        # Second branch (variation)
        self.assertEqual(tree["children"][1]["color"], "B")
        self.assertEqual(tree["children"][1]["coord"], "dd")

    def test_nested_variations(self):
        """Test nested variation branches"""
        sgf = "(;GM[1];B[pd](;W[pp])(;W[dp](;B[dd])(;B[qd])))"
        result = parse_sgf(sgf)

        # Tree structure: Root -> B[pd] -> (W[pp], W[dp] -> (B[dd], B[qd]))
        # B[pd] has 2 children, contributing 1 branch
        # W[dp] has 2 children, contributing 1 branch
        # 2 branches total
        self.assertEqual(result["stats"]["total_nodes"], 6)
        self.assertEqual(result["stats"]["move_nodes"], 5)
        self.assertEqual(result["stats"]["branch_count"], 2)

        tree = result["tree"]
        # Root -> B[pd]
        b_node = tree["children"][0]
        self.assertEqual(b_node["color"], "B")

        # B[pd] has two children: W[pp] and W[dp]
        self.assertEqual(len(b_node["children"]), 2)
        self.assertEqual(b_node["children"][0]["coord"], "pp")
        self.assertEqual(b_node["children"][1]["coord"], "dp")

        # W[dp] has two children: B[dd] and B[qd]
        w_dp_node = b_node["children"][1]
        self.assertEqual(len(w_dp_node["children"]), 2)
        self.assertEqual(w_dp_node["children"][0]["coord"], "dd")
        self.assertEqual(w_dp_node["children"][1]["coord"], "qd")

    def test_escape_chars(self):
        """Test escape characters"""
        sgf = r"(;GM[1]C[Comment \] test])"
        result = parse_sgf(sgf)

        self.assertEqual(result["tree"]["properties"]["C"], "Comment ] test")
        self.assertEqual(len(result["errors"]), 0)

    def test_handicap(self):
        """Test a handicap game"""
        sgf = "(;GM[1]SZ[19]HA[2]AB[pd][dp];W[pp])"
        result = parse_sgf(sgf)

        self.assertEqual(result["game_info"]["handicap"], 2)
        self.assertEqual(len(result["game_info"]["handicap_stones"]), 2)
        self.assertEqual(
            result["game_info"]["handicap_stones"][0], {"x": 15, "y": 3, "color": "B"}
        )
        self.assertEqual(
            result["game_info"]["handicap_stones"][1], {"x": 3, "y": 15, "color": "B"}
        )

        # Handicap positions should be at the root node
        ab = result["tree"]["properties"]["AB"]
        self.assertIsInstance(ab, list)
        self.assertEqual(len(ab), 2)

    def test_multi_value_property(self):
        """Test multi-value properties"""
        sgf = "(;GM[1]AB[aa][bb][cc])"
        result = parse_sgf(sgf)

        ab = result["tree"]["properties"]["AB"]
        self.assertIsInstance(ab, list)
        self.assertEqual(len(ab), 3)
        self.assertEqual(ab[0], "aa")
        self.assertEqual(ab[1], "bb")
        self.assertEqual(ab[2], "cc")

    def test_invalid_sgf(self):
        """Test an invalid SGF (unclosed property value)"""
        sgf = "(;GM[1];B[pd;W[pp)"  # B's property value is unclosed
        result = parse_sgf(sgf)

        # There should be an error (unclosed property value)
        has_error = any("not closed" in err or "Property" in err for err in result["errors"])
        self.assertTrue(has_error or len(result["errors"]) > 0)

    def test_extra_close_paren(self):
        """Test an extra closing parenthesis"""
        sgf = "(;GM[1];B[pd]))"
        result = parse_sgf(sgf)

        # There should be an extra-closing-parenthesis error or a parse error
        has_paren_error = any("parenthesis" in err for err in result["errors"])
        self.assertTrue(has_paren_error or len(result["errors"]) > 0)

    def test_pass_move(self):
        """Test a pass move"""
        sgf = "(;GM[1];B[pd];W[];B[dd])"
        result = parse_sgf(sgf)

        tree = result["tree"]
        w_node = tree["children"][0]["children"][0]
        self.assertEqual(w_node["color"], "W")
        self.assertIsNone(w_node["coord"])

    def test_complex_tree(self):
        """Test a complex tree structure"""
        sgf = """(;GM[1]FF[4]PB[Black]PW[White]
            (;B[pd];W[pp])
            (;B[dd];W[dp]
                (;B[pd])
                (;B[pp];W[pd](;B[qf])(;B[pf]))
            )
            (;B[dp];W[dd])
        )"""
        result = parse_sgf(sgf)

        # The root has 3 children, contributing 2 branches
        # The second branch has sub-branches, so there should be 4 branches total
        self.assertEqual(result["stats"]["branch_count"], 4)

        # Verify game_info
        self.assertEqual(result["game_info"]["black"], "Black")
        self.assertEqual(result["game_info"]["white"], "White")
        self.assertEqual(result["game_info"]["board_size"], 19)

    def test_multigo_format(self):
        """Test a complex game record in MultiGo format (user-provided game)"""
        sgf = """(;CA[gb2312]AP[MultiGo:4.4.4]MULTIGOGM[0]

(;B[pd]N[b1];W[qc];B[qd];W[pc];B[oc];W[ob];B[nb];W[nc];B[od];W[mb];B[pb];W[na];B[qb])
(;B[pd]N[b2];W[qf]
(;B[qe]N[b21];W[pf];B[nd];W[pj])
(;B[nc]N[b22];W[rd];B[qc];W[qi]))
(;B[qd]N[b3];W[oc];B[pc];W[od];B[qf];W[kc]))"""

        result = parse_sgf(sgf)

        # Verify the basic structure
        self.assertEqual(result["stats"]["total_nodes"], 30)
        self.assertEqual(result["stats"]["move_nodes"], 29)
        self.assertEqual(result["stats"]["max_depth"], 13)
        self.assertEqual(result["stats"]["branch_count"], 3)

        # Verify root node properties
        self.assertEqual(result["tree"]["properties"]["CA"], "gb2312")
        self.assertEqual(result["tree"]["properties"]["AP"], "MultiGo:4.4.4")

        # Verify the root has 3 direct children (three branches)
        self.assertEqual(len(result["tree"]["children"]), 3)

        # Verify the b1 branch
        b1 = result["tree"]["children"][0]
        self.assertEqual(b1["properties"]["N"], "b1")
        self.assertEqual(b1["coord"], "pd")
        self.assertEqual(b1["move_number"], 1)

        # Verify the b2 branch and its sub-branches
        b2 = result["tree"]["children"][1]
        self.assertEqual(b2["properties"]["N"], "b2")
        # B[pd] N=b2 -> W[qf] -> (B[qe] N=b21, B[nc] N=b22)
        self.assertEqual(len(b2["children"]), 1)  # W[qf]
        w_qf = b2["children"][0]
        self.assertEqual(len(w_qf["children"]), 2)  # b21, b22 sub-branches

        # Verify the b3 branch
        b3 = result["tree"]["children"][2]
        self.assertEqual(b3["properties"]["N"], "b3")
        self.assertEqual(b3["coord"], "qd")


if __name__ == "__main__":
    print("=" * 60)
    print("SGF Parser Unit Tests")
    print("=" * 60)

    # Create the test suite
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestSGFParser)

    # Run the tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("=" * 60)
    if result.wasSuccessful():
        print("✓ All tests passed")
    else:
        print("✗ Tests failed")
        # Output failure details
        for failure in result.failures + result.errors:
            print(f"\nFailed: {failure[0]}")
            print(failure[1])
    print("=" * 60)
