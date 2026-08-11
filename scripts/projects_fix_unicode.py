import pathlib

p = pathlib.Path("relay/gui/projects.py")
t = p.read_text(encoding="utf-8")
# Replace the literal backslash-u00b7 with the unicode middle dot character (chr(0xB7))
original = r"\u00b7"
replacement = chr(0xB7)
if original in t:
    t = t.replace(original, replacement)
    p.write_text(t, encoding="utf-8")
    print("fixed unicode escapes")
else:
    print("no escape literals found")
