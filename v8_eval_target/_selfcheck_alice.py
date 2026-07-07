"""Temporary self-check for string_utils (will be deleted)."""
from string_utils import (
    reverse, is_palindrome, count_words, capitalize_words, truncate,
)

tests = [
    ('reverse abc', reverse('abc'), 'cba'),
    ('reverse empty', reverse(''), ''),
    ('reverse unicode', reverse('\u4f60\u597d'), '\u597d\u4f60'),
    ('palindrome yes', is_palindrome('A man, a plan, a canal: Panama'), True),
    ('palindrome no', is_palindrome('hello'), False),
    ('palindrome empty', is_palindrome(''), True),
    ('palindrome punct only', is_palindrome('!!!'), True),
    ('count basic', count_words('hello world'), 2),
    ('count multi space', count_words('  hello   world  '), 2),
    ('count empty', count_words(''), 0),
    ('count ws only', count_words('   '), 0),
    ('cap basic', capitalize_words('hello world'), 'Hello World'),
    ('cap mixed', capitalize_words('hELLo WoRLD'), 'Hello World'),
    ('cap collapse', capitalize_words('a  b'), 'a b'),
    ('cap empty', capitalize_words(''), ''),
    ('trunc no change', truncate('hi', 10), 'hi'),
    ('trunc exact', truncate('hello', 5), 'hello'),
    ('trunc default', truncate('hello world', 8), 'hello...'),
    ('trunc suffix bigger', truncate('hello world', 2), 'he'),
    ('trunc custom suffix len', len(truncate('hello world', 7, suffix='...')), 7),
]
fail = 0
for name, actual, expected in tests:
    if actual != expected:
        fail += 1
        print('FAIL', name, '->', repr(actual), '!=', repr(expected))

# error cases
for fn, args in [
    (reverse, (123,)),
    (is_palindrome, (None,)),
    (count_words, ([],)),
    (capitalize_words, (1.0,)),
    (truncate, (1, 5)),
]:
    try:
        fn(*args)
        print('FAIL type err on', fn.__name__)
        fail += 1
    except TypeError:
        pass
try:
    truncate('x', -1)
    print('FAIL value err')
    fail += 1
except ValueError:
    pass
try:
    truncate('x', True)
    print('FAIL bool-as-int')
    fail += 1
except TypeError:
    pass

print(f'DONE fail={fail} total_positive={len(tests)} total_negative=7')
