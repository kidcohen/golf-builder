"""WebSocket, so a browser can reach the simulator.

A browser cannot open a TCP socket. That is the whole reason this exists: the
protocol is already exactly right - one JSON object per line, both directions -
and a WebGL build simply has no way to speak it over a plain socket.

Rather than a second server, this is a wrapper. A connection arrives, and if it
opens with an HTTP upgrade it gets dressed in a reader and a writer that behave
like the stream ones: `readline` returns the next message, `write` takes bytes,
a newline ends a message. Everything above this - joining, shots, scoring,
reconnection, the lot - carries on unable to tell the difference, which is the
point. There is one server and one protocol, reachable two ways.

Implemented here rather than taken from a library because the project runs on
the standard library and numpy, and the useful half of RFC 6455 is a handshake
and a frame header. Continuation frames are handled; compression extensions are
not negotiated, so none arrive.
"""

import asyncio
import base64
import hashlib
import struct

# From the RFC. Concatenated with the client's key and hashed, it proves to the
# browser that it reached something that understands WebSocket rather than an
# HTTP server that answered 101 by accident.
MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

CONTINUATION, TEXT, BINARY, CLOSE, PING, PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA
MAX_MESSAGE_BYTES = 4 * 1024 * 1024   # a trajectory is large; a lie is larger
HANDSHAKE_LIMIT = 16 * 1024


class ProtocolFault(Exception):
    """The other end is not speaking WebSocket properly."""


def accept_key(client_key):
    """The answer to the browser's challenge."""
    digest = hashlib.sha1((client_key + MAGIC).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


class _ReplayingFirstLine:
    """A reader that hands back a line already taken out of it.

    Telling the two protocols apart costs one line, and a plain client's first
    line is a message it expects to be acted on. Rather than push bytes back -
    a stream buffer appends, so anything returned to it arrives after the rest
    of the line and quietly corrupts it - the line is simply carried here and
    handed over on the next read.
    """

    def __init__(self, line, reader):
        self._line = line
        self._reader = reader

    async def readline(self):
        if self._line is not None:
            line, self._line = self._line, None
            return line
        return await self._reader.readline()

    async def readexactly(self, count):
        return await self._reader.readexactly(count)

    def at_eof(self):
        return self._line is None and self._reader.at_eof()


async def _read_headers(reader):
    """The rest of an HTTP request, the opening line having been read."""
    headers = {}
    read = 0
    while True:
        line = await reader.readline()
        read += len(line)
        if read > HANDSHAKE_LIMIT:
            raise ProtocolFault("handshake too large")
        if line in (b"\r\n", b"\n", b""):
            return headers
        name, _, value = line.decode("latin-1").partition(":")
        headers[name.strip().lower()] = value.strip()


async def handshake(opening, reader, writer):
    """Answer the browser. `opening` is the request line already read.

    Both protocols are line-based, which is what makes telling them apart
    cheap: an HTTP request announces itself in its first word, and a client
    speaking the plain protocol has sent a JSON object instead.
    """
    try:
        headers = await asyncio.wait_for(_read_headers(reader), 5.0)
    except (asyncio.IncompleteReadError, asyncio.TimeoutError, ValueError) as fault:
        raise ProtocolFault(f"could not read the handshake: {fault}")

    key = headers.get("sec-websocket-key")
    if not key or "websocket" not in headers.get("upgrade", "").lower():
        writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        await writer.drain()
        raise ProtocolFault("an HTTP request that was not a WebSocket upgrade")

    writer.write(
        b"HTTP/1.1 101 Switching Protocols\r\n"
        b"Upgrade: websocket\r\n"
        b"Connection: Upgrade\r\n"
        b"Sec-WebSocket-Accept: " + accept_key(key).encode("ascii") + b"\r\n\r\n"
    )
    await writer.drain()
    return True


def frame(payload, opcode=TEXT):
    """One frame, unmasked - which is what a server must send."""
    head = bytes([0x80 | opcode])
    size = len(payload)
    if size < 126:
        head += bytes([size])
    elif size < 65536:
        head += bytes([126]) + struct.pack("!H", size)
    else:
        head += bytes([127]) + struct.pack("!Q", size)
    return head + payload


class WebSocketReader:
    """Presents WebSocket messages as if they were newline-delimited lines.

    The server above reads with `readline`, so that is what this offers. One
    WebSocket message becomes one line, which is the same shape the protocol
    already had - a browser sends one JSON object per message rather than per
    line, and neither end has to care which.
    """

    def __init__(self, reader, writer=None):
        self._reader = reader
        # Held only to answer a ping. The standard says a pong is owed, and an
        # unanswered ping is how a proxy decides a connection has died and
        # takes it away mid-round.
        self._writer = writer
        self._pending = b""
        self._closed = False

    async def readline(self):
        if self._pending:
            line, self._pending = self._pending, b""
            return line
        message = await self._read_message()
        return b"" if message is None else message + b"\n"

    async def _read_message(self):
        """The next complete message, reassembling continuations."""
        collected = b""
        first_opcode = None
        while True:
            header = await self._reader.readexactly(2)
            final = bool(header[0] & 0x80)
            opcode = header[0] & 0x0F
            masked = bool(header[1] & 0x80)
            size = header[1] & 0x7F

            if size == 126:
                size = struct.unpack("!H", await self._reader.readexactly(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", await self._reader.readexactly(8))[0]
            if size > MAX_MESSAGE_BYTES:
                raise ProtocolFault(f"message of {size} bytes is beyond reason")

            # Every frame from a browser is masked. One that is not is either a
            # broken client or something that is not a browser at all.
            mask = await self._reader.readexactly(4) if masked else b""
            body = await self._reader.readexactly(size) if size else b""
            if masked:
                body = bytes(b ^ mask[i % 4] for i, b in enumerate(body))

            if opcode == CLOSE:
                self._closed = True
                return None
            if opcode == PING:
                if self._writer is not None and not self._writer.is_closing():
                    self._writer.write(frame(body, PONG))
                continue
            if opcode == PONG:
                continue

            if first_opcode is None and opcode in (TEXT, BINARY):
                first_opcode = opcode
            collected += body
            if final:
                return collected

    def at_eof(self):
        return self._closed or self._reader.at_eof()


class WebSocketWriter:
    """Takes the bytes the server writes and sends them as messages.

    The server writes a JSON object followed by a newline. That newline is what
    marks the end of a message on a plain socket; here it marks the end of a
    frame instead, and is not sent - a browser gets one message per object,
    which is what a browser expects.
    """

    def __init__(self, writer):
        self._writer = writer
        self._buffer = b""

    def write(self, data):
        self._buffer += data
        while b"\n" in self._buffer:
            line, _, self._buffer = self._buffer.partition(b"\n")
            if line:
                self._writer.write(frame(line, TEXT))

    async def drain(self):
        await self._writer.drain()

    def is_closing(self):
        return self._writer.is_closing()

    def close(self):
        try:
            self._writer.write(frame(b"", CLOSE))
        except Exception:
            pass
        self._writer.close()

    async def wait_closed(self):
        await self._writer.wait_closed()

    def get_extra_info(self, *args, **kwargs):
        return self._writer.get_extra_info(*args, **kwargs)


async def wrap(reader, writer):
    """Dress a connection as a WebSocket if that is what it is.

    Returns the pair to use. A plain client comes back with its first line
    intact, so one port serves a desktop build and a browser build without
    either being told which it is talking to, and without a desktop client
    losing so much as a byte to the question being asked.
    """
    try:
        opening = await asyncio.wait_for(reader.readline(), 10.0)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError):
        return reader, writer
    if not opening:
        return reader, writer

    if opening.startswith(b"GET "):
        await handshake(opening, reader, writer)
        return WebSocketReader(reader, writer), WebSocketWriter(writer)
    return _ReplayingFirstLine(opening, reader), writer
