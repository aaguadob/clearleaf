from clearleaf._lexer import Lexer


def tokens(data: bytes):
    lx = Lexer(data)
    out = []
    while True:
        tok = lx.next_token()
        if tok.kind == "eof":
            break
        out.append((tok.kind, tok.value))
    return out


def test_integers_and_reals():
    assert tokens(b"123 -45 +8 0 3.14 -0.5 4. .5") == [
        ("num", 123),
        ("num", -45),
        ("num", 8),
        ("num", 0),
        ("num", 3.14),
        ("num", -0.5),
        ("num", 4.0),
        ("num", 0.5),
    ]


def test_name_tokens_and_hex_escapes():
    assert tokens(b"/Type /Pages /A#42 /Name#20With#20Spaces") == [
        ("name", "Type"),
        ("name", "Pages"),
        ("name", "AB"),
        ("name", "Name With Spaces"),
    ]


def test_literal_string_escapes_and_nesting():
    assert tokens(b"(hi)") == [("string", b"hi")]
    assert tokens(b"(a (nested) b)") == [("string", b"a (nested) b")]
    assert tokens(rb"(line1\nline2\ttab\)paren)") == [
        ("string", b"line1\nline2\ttab)paren")
    ]
    assert tokens(rb"(octal \101\102\103)") == [("string", b"octal ABC")]


def test_literal_string_line_continuation_and_eol_normalization():
    assert tokens(b"(a\\\nb)") == [("string", b"ab")]
    assert tokens(b"(a\r\nb)") == [("string", b"a\nb")]
    assert tokens(b"(a\rb)") == [("string", b"a\nb")]


def test_hex_string():
    assert tokens(b"<48656C6C6F>") == [("hexstring", b"Hello")]
    assert tokens(b"<48 65 6C 6C 6F>") == [("hexstring", b"Hello")]
    # 9 hex digits (odd): last one is padded with a trailing "0" -> ...60 = '`'
    assert tokens(b"<48656C6C6>") == [("hexstring", b"Hell`")]


def test_delimiters_and_keywords():
    assert tokens(b"<< /A 1 >> [1 2] true false null") == [
        ("dict_start", None),
        ("name", "A"),
        ("num", 1),
        ("dict_end", None),
        ("array_start", None),
        ("num", 1),
        ("num", 2),
        ("array_end", None),
        ("keyword", "true"),
        ("keyword", "false"),
        ("keyword", "null"),
    ]


def test_comments_are_skipped():
    assert tokens(b"1 % a comment\n2") == [("num", 1), ("num", 2)]


def test_push_back():
    lx = Lexer(b"1 2 3")
    t1 = lx.next_token()
    t2 = lx.next_token()
    lx.push_back(t2)
    lx.push_back(t1)
    assert lx.next_token() == t1
    assert lx.next_token() == t2
    assert lx.next_token().value == 3
