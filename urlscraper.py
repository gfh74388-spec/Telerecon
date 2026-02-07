import asyncio
import os
import re
from urllib.parse import urlparse

from colorama import Fore, Style
from telethon import TelegramClient
from telethon.errors import RPCError
from telethon.tl.types import Channel, Chat, MessageEntityTextUrl

import details as ds

# Login details
api_id = ds.apiID
api_hash = ds.apiHash
phone = ds.number


URL_REGEX = re.compile(r'https?://t\.me/[^\s)>\]]+')


def normalize_telegram_link(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.netloc not in {'t.me', 'www.t.me', 'telegram.me', 'www.telegram.me'}:
        return None
    path = parsed.path.strip('/')
    if not path:
        return None
    parts = path.split('/')
    if parts[0] == 'c':
        return None
    if len(parts) > 1 and parts[1].isdigit():
        return None
    if parts[0].startswith('+'):
        return f'https://t.me/{parts[0]}'
    return f'https://t.me/{parts[0]}'


def read_seed_list() -> list[str]:
    print(
        f'{Fore.CYAN}Enter a Telegram channel/group link or a file '
        f'path (txt/csv) with one link per line.{Style.RESET_ALL}'
    )
    seed_input = input('Seed link or file path: ').strip()
    if os.path.isfile(seed_input):
        with open(seed_input, 'r', encoding='utf-8') as file:
            seeds = [line.strip() for line in file if line.strip()]
    else:
        seeds = [seed_input] if seed_input else []
    return seeds


def classify_entity(entity) -> tuple[str, bool]:
    is_topics = bool(getattr(entity, 'forum', False))
    if isinstance(entity, Channel):
        if getattr(entity, 'broadcast', False):
            return 'channel', is_topics
        if getattr(entity, 'megagroup', False):
            return 'group', is_topics
    if isinstance(entity, Chat):
        return 'group', is_topics
    return 'unknown', is_topics


async def collect_links_from_entity(client: TelegramClient, entity, max_results: int) -> set[str]:
    urls: set[str] = set()
    async for message in client.iter_messages(
        entity,
        search='https://t.me/',
        limit=max_results,
    ):
        if message.entities:
            for entity_item in message.entities:
                if isinstance(entity_item, MessageEntityTextUrl):
                    if normalized := normalize_telegram_link(entity_item.url):
                        urls.add(normalized)
        if message.text:
            for match in URL_REGEX.findall(message.text):
                if normalized := normalize_telegram_link(match):
                    urls.add(normalized)
    return urls


async def main():
    client = TelegramClient(phone, api_id, api_hash)

    await client.start()

    if not await client.is_user_authorized():
        await client.send_code_request(phone)
        await client.sign_in(phone, input('Enter the code: '))

    seeds = read_seed_list()
    if not seeds:
        print(f'{Fore.RED}No seeds provided. Exiting.{Style.RESET_ALL}')
        return

    max_results = int(
        input('Max messages to search per channel/group (e.g. 500): ').strip() or '500'
    )
    depth_limit = int(
        input('Depth (1=seeds only, 2=search links inside seeds, etc): ').strip() or '2'
    )

    visited: set[str] = set()
    all_links: set[str] = set()
    queue: list[tuple[str, int]] = []
    for seed in seeds:
        if normalized := normalize_telegram_link(seed):
            queue.append((normalized, 1))
            visited.add(normalized)

    categorized = {
        'channel': set(),
        'group': set(),
        'topics_group': set(),
        'invite': set(),
        'unknown': set(),
    }

    while queue:
        current_link, depth = queue.pop(0)
        print(f'{Fore.CYAN}Scanning {current_link} (depth {depth})...{Style.RESET_ALL}')
        if '/+' in current_link:
            categorized['invite'].add(current_link)
            continue
        try:
            entity = await client.get_entity(current_link)
        except (ValueError, RPCError) as exc:
            print(f'{Fore.YELLOW}Skipping {current_link}: {exc}{Style.RESET_ALL}')
            categorized['unknown'].add(current_link)
            continue

        kind, is_topics = classify_entity(entity)
        if kind == 'group' and is_topics:
            categorized['topics_group'].add(current_link)
        elif kind == 'group':
            categorized['group'].add(current_link)
        elif kind == 'channel':
            categorized['channel'].add(current_link)
        else:
            categorized['unknown'].add(current_link)

        if depth > depth_limit:
            continue

        found_links = await collect_links_from_entity(client, entity, max_results)
        for link in found_links:
            all_links.add(link)
            if link not in visited:
                visited.add(link)
                if depth < depth_limit:
                    queue.append((link, depth + 1))

    urls_folder = 'URLs'
    os.makedirs(urls_folder, exist_ok=True)

    output_filename = os.path.join(urls_folder, 'discovered_links.csv')
    with open(output_filename, 'w', encoding='utf-8') as file:
        file.write('\n'.join(sorted(all_links)))

    def write_category(name: str, items: set[str]) -> None:
        filepath = os.path.join(urls_folder, f'{name}.csv')
        with open(filepath, 'w', encoding='utf-8') as file:
            file.write('\n'.join(sorted(items)))
        print(f'{name} saved to: {filepath}')

    write_category('channels', categorized['channel'])
    write_category('groups', categorized['group'])
    write_category('topics_groups', categorized['topics_group'])
    write_category('invites', categorized['invite'])
    write_category('unknown', categorized['unknown'])

    print(f'All links saved to: {output_filename}')


if __name__ == '__main__':
    asyncio.run(main())
