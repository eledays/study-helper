"""Split Telegram HTML while keeping tags balanced and UTF-16 size bounded."""
from html import escape
from html.parser import HTMLParser


def split_text(text, html=False, limit=3500):
    class Splitter(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack, self.chunks, self.buffer, self.size = [], [], '', 0

        def flush(self):
            if self.size:
                self.chunks.append(self.buffer + ''.join(f'</{tag}>' for tag, _ in reversed(self.stack)))
                self.buffer = ''.join(raw for _, raw in self.stack)
                self.size = 0

        def handle_starttag(self, tag, attrs):
            raw = self.get_starttag_text()
            self.stack.append((tag, raw))
            self.buffer += raw

        def handle_endtag(self, tag):
            self.buffer += f'</{tag}>'
            if self.stack and self.stack[-1][0] == tag:
                self.stack.pop()

        def handle_data(self, data):
            for character in data:
                size = len(character.encode('utf-16-le')) // 2
                if self.size + size > limit:
                    self.flush()
                self.buffer += escape(character, quote=False) if html else character
                self.size += size

    parser = Splitter()
    if html:
        parser.feed(text)
        parser.close()
    else:
        parser.handle_data(text)
    parser.flush()
    return parser.chunks or [text]
