import mimetypes
import re
from typing import AsyncGenerator, List, Optional, Tuple
from urllib.parse import quote

from fastapi import HTTPException, status
from fastapi.responses import Response, StreamingResponse
from telethon import TelegramClient

from .config import settings
from .models import File, FilePart

RANGE_HEADER_PATTERN = re.compile(r"^bytes=(\d*)-(\d*)$")


def parse_range_header(range_header: Optional[str], total_size: int) -> Optional[Tuple[int, int]]:
    """
    Parses an RFC 7233 Range header string.
    Returns (start, end) as 0-indexed inclusive byte offsets or None.
    Raises HTTPException 416 if requested range cannot be satisfied.
    """
    if not range_header:
        return None

    range_header = range_header.strip()
    match = RANGE_HEADER_PATTERN.match(range_header)
    if not match:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{total_size}"},
        )

    raw_start, raw_end = match.groups()

    if not raw_start and not raw_end:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{total_size}"},
        )

    if not raw_start:
        suffix_length = int(raw_end)
        if suffix_length == 0:
            raise HTTPException(
                status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                headers={"Content-Range": f"bytes */{total_size}"},
            )
        start = max(0, total_size - suffix_length)
        end = total_size - 1
    elif not raw_end:
        start = int(raw_start)
        end = total_size - 1
    else:
        start = int(raw_start)
        end = int(raw_end)

    if start > end or start >= total_size:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{total_size}"},
        )

    end = min(end, total_size - 1)
    return start, end


class PartSegment:
    def __init__(self, part: FilePart, local_start: int, bytes_to_read: int):
        self.part = part
        self.local_start = local_start
        self.bytes_to_read = bytes_to_read


def compute_part_segments(
    parts: List[FilePart], req_start: int, req_end: int
) -> List[PartSegment]:
    """
    Determines which parts overlap the global [req_start, req_end] range
    and calculates each part's local starting byte offset and byte count.
    """
    segments: List[PartSegment] = []
    current_global_offset = 0

    for part in parts:
        part_start = current_global_offset
        part_end = current_global_offset + part.size - 1
        current_global_offset += part.size

        # Skip parts that don't overlap requested window
        if part_end < req_start or part_start > req_end:
            continue

        local_start = max(0, req_start - part_start)
        local_end = min(part.size - 1, req_end - part_start)
        bytes_to_read = local_end - local_start + 1

        segments.append(
            PartSegment(
                part=part,
                local_start=local_start,
                bytes_to_read=bytes_to_read,
            )
        )

    return segments


async def stream_telegram_part(
    client: TelegramClient,
    chat_id: int,
    message_id: int,
    local_offset: int,
    bytes_needed: int,
) -> AsyncGenerator[bytes, None]:
    """
    Streams a bounded slice of a single Telegram media file.
    """
    message = await client.get_messages(chat_id, ids=message_id)
    if not message or not message.media:
        raise RuntimeError(f"Message {message_id} in {chat_id} does not contain downloadable media.")

    media = message.media
    remaining = bytes_needed

    async for chunk in client.iter_download(
        media,
        offset=local_offset,
        request_size=settings.chunk_size,
    ):
        if remaining <= 0:
            break

        if len(chunk) > remaining:
            yield chunk[:remaining]
            remaining = 0
            break
        else:
            yield chunk
            remaining -= len(chunk)


async def virtual_file_streamer(
    client: TelegramClient,
    segments: List[PartSegment],
) -> AsyncGenerator[bytes, None]:
    """
    Sequentially iterates over all computed part segments and streams content.
    """
    for segment in segments:
        async for chunk in stream_telegram_part(
            client=client,
            chat_id=segment.part.telegram_chat_id,
            message_id=segment.part.telegram_message_id,
            local_offset=segment.local_start,
            bytes_needed=segment.bytes_to_read,
        ):
            yield chunk


def build_streaming_response(
    client: TelegramClient,
    file_record: File,
    range_header: Optional[str],
) -> Response:
    total_size = file_record.total_size

    if total_size == 0:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    parsed_range = parse_range_header(range_header, total_size)

    if parsed_range is not None:
        start, end = parsed_range
        status_code = status.HTTP_206_PARTIAL_CONTENT
        content_length = end - start + 1
        content_range = f"bytes {start}-{end}/{total_size}"
    else:
        start, end = 0, total_size - 1
        status_code = status.HTTP_200_OK
        content_length = total_size
        content_range = None

    segments = compute_part_segments(file_record.parts, start, end)

    mime_type, _ = mimetypes.guess_type(file_record.filename)
    if not mime_type:
        mime_type = "application/octet-stream"

    safe_filename = quote(file_record.filename)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Type": mime_type,
        "Content-Disposition": f"attachment; filename*=UTF-8''{safe_filename}",
    }

    if content_range:
        headers["Content-Range"] = content_range

    return StreamingResponse(
        virtual_file_streamer(client, segments),
        status_code=status_code,
        headers=headers,
        media_type=mime_type,
    )
