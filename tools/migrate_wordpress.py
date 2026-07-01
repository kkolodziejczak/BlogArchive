#!/usr/bin/env python3
"""Generate a static GitHub Pages archive from a WordPress WXR export."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import posixpath
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "excerpt": "http://wordpress.org/export/1.2/excerpt/",
    "wp": "http://wordpress.org/export/1.2/",
}

SITE_HOST = "kkolodziejczak.net"
SITE_ORIGINS = (f"https://{SITE_HOST}", f"http://{SITE_HOST}")
FAVICON_PATH = "assets/wp-content/uploads/2017/07/FavIcon-2.png"
FAVICON_ICO_PATH = "favicon.ico"


@dataclass
class Entry:
    id: str
    kind: str
    title: str
    slug: str
    source_url: str
    output_path: str
    date: dt.datetime
    modified: str
    author: str
    content: str
    excerpt: str
    categories: list[dict[str, str]]
    tags: list[dict[str, str]]
    metas: dict[str, str]
    thumbnail_url: str | None = None
    external_url: str | None = None


def text(node: ET.Element, path: str, default: str = "") -> str:
    return node.findtext(path, default=default, namespaces=NS) or default


def parse_date(value: str) -> dt.datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            parsed = dt.datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=None)
        except ValueError:
            continue
    return dt.datetime.min


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9\-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or "untitled"


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def strip_tags(value: str) -> str:
    value = re.sub(r"<script\b.*?</script>", "", value, flags=re.I | re.S)
    value = re.sub(r"<style\b.*?</style>", "", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def rel_url(from_file: str, target: str) -> str:
    from_dir = posixpath.dirname(from_file) or "."
    rel = posixpath.relpath(target, from_dir)
    if rel == ".":
        return "./"
    return rel.replace("\\", "/")


def canonical_url(path: str) -> str:
    clean = path.replace("index.html", "")
    return f"https://{SITE_HOST}/{clean}"


def parse_meta(item: ET.Element) -> dict[str, str]:
    metas: dict[str, str] = {}
    for meta in item.findall("wp:postmeta", NS):
        key = text(meta, "wp:meta_key")
        value = text(meta, "wp:meta_value")
        if key:
            metas[key] = value
    return metas


def parse_terms(item: ET.Element) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    categories: list[dict[str, str]] = []
    tags: list[dict[str, str]] = []
    for cat in item.findall("category"):
        domain = cat.attrib.get("domain", "")
        term = {
            "name": cat.text or "",
            "slug": cat.attrib.get("nicename") or slugify(cat.text or ""),
        }
        if domain == "category":
            categories.append(term)
        elif domain == "post_tag":
            tags.append(term)
    return categories, tags


def output_path_for(item: ET.Element) -> str:
    kind = text(item, "wp:post_type")
    slug = text(item, "wp:post_name") or slugify(text(item, "title"))
    if kind == "post":
        date = parse_date(text(item, "wp:post_date") or text(item, "pubDate"))
        return f"{date:%Y/%m/%d}/{slug}/index.html"
    return f"{slug}/index.html"


def parse_export(export_path: Path) -> tuple[list[Entry], dict[str, str], list[str]]:
    root = ET.parse(export_path).getroot()
    channel = root.find("channel")
    if channel is None:
        raise ValueError("Export does not contain an RSS channel.")

    attachment_urls: dict[str, str] = {}
    download_urls: set[str] = set()

    for item in channel.findall("item"):
        if text(item, "wp:post_type") != "attachment":
            continue
        post_id = text(item, "wp:post_id")
        url = text(item, "wp:attachment_url")
        if post_id and url:
            attachment_urls[post_id] = url
            download_urls.add(normalize_media_url(url))

    entries: list[Entry] = []
    for item in channel.findall("item"):
        kind = text(item, "wp:post_type")
        status = text(item, "wp:status")
        if status != "publish" or kind not in {"post", "page"}:
            continue

        metas = parse_meta(item)
        source_url = (item.findtext("link") or "").strip()
        output_path = output_path_for(item)
        categories, tags = parse_terms(item)
        content = text(item, "content:encoded")
        excerpt = text(item, "excerpt:encoded") or metas.get("_yoast_wpseo_metadesc", "")

        for url in extract_media_urls(content):
            download_urls.add(normalize_media_url(url))
        if metas.get("_links_to", "").startswith(SITE_ORIGINS):
            download_urls.add(normalize_media_url(metas["_links_to"]))

        thumbnail_url = attachment_urls.get(metas.get("_thumbnail_id", ""))
        if thumbnail_url:
            download_urls.add(normalize_media_url(thumbnail_url))

        external_url = metas.get("_links_to") or None
        if external_url and external_url.startswith(source_url):
            external_url = None

        entries.append(
            Entry(
                id=text(item, "wp:post_id"),
                kind=kind,
                title=text(item, "title") or "(untitled)",
                slug=text(item, "wp:post_name") or slugify(text(item, "title")),
                source_url=source_url,
                output_path=output_path,
                date=parse_date(text(item, "wp:post_date") or text(item, "pubDate")),
                modified=text(item, "wp:post_modified"),
                author=text(item, "dc:creator"),
                content=content,
                excerpt=excerpt,
                categories=categories,
                tags=tags,
                metas=metas,
                thumbnail_url=thumbnail_url,
                external_url=external_url,
            )
        )

    permalink_map: dict[str, str] = {}
    for entry in entries:
        for url in {entry.source_url, entry.source_url.rstrip("/") + "/"}:
            parsed = urllib.parse.urlparse(url)
            if parsed.netloc == SITE_HOST:
                permalink_map[parsed.path.rstrip("/") + "/"] = entry.output_path

    return entries, attachment_urls, sorted(download_urls)


def extract_media_urls(content: str) -> list[str]:
    if not content:
        return []
    pattern = rf"https?://{re.escape(SITE_HOST)}/wp-content/uploads/[^\s\"'<>),\]]+"
    return [html.unescape(url) for url in re.findall(pattern, content)]


def normalize_media_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc == SITE_HOST and parsed.path.startswith("/wp-content/uploads/"):
        return urllib.parse.urlunparse(("https", parsed.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    return url


def local_media_target(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc != SITE_HOST or not parsed.path.startswith("/wp-content/uploads/"):
        return None
    decoded_path = urllib.parse.unquote(parsed.path.lstrip("/"))
    return "assets/" + decoded_path


def safe_download_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(urllib.parse.unquote(parsed.path), safe="/%")
    query = urllib.parse.quote_plus(parsed.query, safe="=&")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, query, parsed.fragment))


def download_assets(
    urls: list[str],
    output_dir: Path,
    retries: int = 0,
    timeout: int = 8,
    max_assets: int | None = None,
) -> tuple[int, list[str]]:
    downloaded = 0
    failed: list[str] = []
    opener = urllib.request.build_opener()
    opener.addheaders = [("User-Agent", "BlogArchiveMigration/1.0")]

    eligible_urls = [url for url in urls if local_media_target(url)]
    if max_assets is not None:
        eligible_urls = eligible_urls[:max_assets]

    total = len(eligible_urls)
    for index, url in enumerate(eligible_urls, start=1):
        target = local_media_target(url)
        target_path = output_dir / target
        if target_path.exists() and target_path.stat().st_size > 0:
            print(f"[{index}/{total}] exists {target}", flush=True)
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"[{index}/{total}] downloading {url}", flush=True)

        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                with opener.open(safe_download_url(url), timeout=timeout) as response:
                    target_path.write_bytes(response.read())
                downloaded += 1
                last_error = None
                print(f"[{index}/{total}] saved {target}", flush=True)
                break
            except Exception as exc:  # noqa: BLE001 - report all download failures.
                last_error = exc
                print(f"[{index}/{total}] failed attempt {attempt + 1}: {exc}", flush=True)
                time.sleep(0.5 + attempt)
        if last_error is not None:
            failed.append(f"{url} ({last_error})")

    return downloaded, failed


def normalize_pre_blocks(content: str) -> str:
    def repl(match: re.Match[str]) -> str:
        attrs = match.group(1)
        body = match.group(2).strip("\n")
        lang = ""
        title = ""

        class_match = re.search(r'class=["\']([^"\']+)["\']', attrs, flags=re.I)
        title_match = re.search(r'title=["\']([^"\']+)["\']', attrs, flags=re.I)
        if class_match:
            lang_match = re.search(r"lang:([a-zA-Z0-9_+#.-]+)", class_match.group(1))
            if lang_match:
                lang = lang_match.group(1).lower()
        if title_match:
            title = html.unescape(title_match.group(1))

        language = {
            "csharp": "csharp",
            "cs": "csharp",
            "xaml": "xml",
            "html": "xml",
            "txt": "plaintext",
        }.get(lang, lang or "plaintext")

        title_attr = f' data-title="{esc(title)}"' if title else ""
        return (
            f'<pre class="code-block language-{esc(language)}"{title_attr}>'
            f'<code class="language-{esc(language)}">{html.escape(body)}</code></pre>'
        )

    return re.sub(r"<pre\b([^>]*)>(.*?)</pre>", repl, content, flags=re.I | re.S)


def convert_shortcodes(content: str) -> str:
    def caption_repl(match: re.Match[str]) -> str:
        body = match.group(1).strip()
        media = re.match(r"(?P<media>(?:<a\b.*?</a>|<img\b[^>]*>))\s*(?P<caption>.*)", body, flags=re.I | re.S)
        if media and media.group("caption").strip():
            return (
                '<figure class="wp-caption">'
                f'{media.group("media").strip()}'
                f'<figcaption>{media.group("caption").strip()}</figcaption>'
                "</figure>"
            )
        return f'<figure class="wp-caption">{body}</figure>'

    content = re.sub(r"\[caption[^\]]*\](.*?)\[/caption\]", caption_repl, content, flags=re.I | re.S)
    content = re.sub(
        r'\[KodNaGitLink\s+name="([^"]+)"\s+link="([^"]+)"\s*\]',
        lambda m: (
            '<p class="source-link"><a href="'
            + esc(m.group(2))
            + '">Kod na GitHub: '
            + esc(m.group(1))
            + "</a></p>"
        ),
        content,
        flags=re.I,
    )
    content = re.sub(r'\[icon\s+name="smile-o"[^\]]*\]', ":)", content, flags=re.I)
    content = re.sub(r"\[/?(?:gallery|embed)[^\]]*\]", "", content, flags=re.I)
    return content


def rewrite_urls(content: str, current_file: str, permalink_map: dict[str, str]) -> str:
    def replace_url(match: re.Match[str]) -> str:
        url = html.unescape(match.group(0))
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc != SITE_HOST:
            return match.group(0)

        media_target = local_media_target(url)
        if media_target:
            return rel_url(current_file, media_target)

        old_path = parsed.path.rstrip("/") + "/"
        if old_path in permalink_map:
            return rel_url(current_file, permalink_map[old_path])
        tag_match = re.fullmatch(r"/tag/([^/]+)/", old_path)
        if tag_match:
            return rel_url(current_file, f"tags/{tag_match.group(1)}/index.html")
        category_match = re.fullmatch(r"/category/([^/]+)/", old_path)
        if category_match:
            return rel_url(current_file, f"categories/{category_match.group(1)}/index.html")
        if old_path == "/":
            return rel_url(current_file, "index.html")
        return url

    site_url_pattern = rf"https?://{re.escape(SITE_HOST)}[^\s\"'<>),\]]*"
    return re.sub(site_url_pattern, replace_url, content)


def render_layout(title: str, body: str, current_file: str, description: str = "") -> str:
    css = rel_url(current_file, "assets/site.css")
    js = rel_url(current_file, "assets/site.js")
    favicon = rel_url(current_file, FAVICON_PATH)
    favicon_ico = rel_url(current_file, FAVICON_ICO_PATH)
    home = rel_url(current_file, "index.html")
    meta_description = strip_tags(description)[:155] or "Static archive of kkolodziejczak.net."
    return f"""<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)}</title>
  <meta name="description" content="{esc(meta_description)}">
  <link rel="icon" href="{favicon}" type="image/png">
  <link rel="shortcut icon" href="{favicon_ico}" type="image/x-icon">
  <link rel="stylesheet" href="{css}">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/styles/github-dark.min.css">
</head>
<body>
  <header class="site-header">
    <a class="site-title" href="{home}">kkolodziejczak</a>
    <nav aria-label="Main">
      <a href="{home}">Archive</a>
      <a href="{rel_url(current_file, 'feed.xml')}">RSS</a>
    </nav>
  </header>
  <main>
{body}
  </main>
  <footer class="site-footer">
    <p>Static archive migrated from WordPress. Original domain: kkolodziejczak.net.</p>
  </footer>
  <script src="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/highlight.min.js"></script>
  <script>window.hljs && hljs.highlightAll();</script>
  <script src="{js}"></script>
</body>
</html>
"""


def render_term_links(entry: Entry, current_file: str) -> str:
    parts: list[str] = []
    for cat in entry.categories:
        parts.append(f'<a href="{rel_url(current_file, "categories/" + cat["slug"] + "/index.html")}">{esc(cat["name"])}</a>')
    for tag in entry.tags:
        parts.append(f'<a href="{rel_url(current_file, "tags/" + tag["slug"] + "/index.html")}">#{esc(tag["name"])}</a>')
    return " ".join(parts)


def prepare_content(entry: Entry, permalink_map: dict[str, str]) -> str:
    content = entry.content
    content = content.replace("<!--more-->", '<hr class="more-break">')
    content = normalize_pre_blocks(content)
    content = convert_shortcodes(content)
    content = rewrite_urls(content, entry.output_path, permalink_map)
    return content


def render_entry(entry: Entry, entries: list[Entry], permalink_map: dict[str, str]) -> str:
    body_content = prepare_content(entry, permalink_map)
    terms = render_term_links(entry, entry.output_path)
    thumbnail = ""
    if entry.thumbnail_url:
        media_target = local_media_target(entry.thumbnail_url)
        if media_target:
            thumbnail = f'<img class="hero-image" src="{rel_url(entry.output_path, media_target)}" alt="">'
    external = ""
    if entry.external_url and entry.external_url != entry.source_url:
        external = f'<p class="external-link"><a href="{esc(entry.external_url)}">Original linked resource</a></p>'

    same_kind = [e for e in entries if e.kind == entry.kind]
    same_kind.sort(key=lambda e: e.date)
    idx = same_kind.index(entry)
    prev_link = ""
    next_link = ""
    if idx > 0:
        prev = same_kind[idx - 1]
        prev_link = f'<a href="{rel_url(entry.output_path, prev.output_path)}">Previous: {esc(prev.title)}</a>'
    if idx < len(same_kind) - 1:
        nxt = same_kind[idx + 1]
        next_link = f'<a href="{rel_url(entry.output_path, nxt.output_path)}">Next: {esc(nxt.title)}</a>'

    body = f"""    <article class="post">
      <header class="post-header">
        <p class="eyebrow">{esc(entry.kind.title())} archive</p>
        <h1>{esc(entry.title)}</h1>
        <div class="post-meta">
          <time datetime="{entry.date:%Y-%m-%d}">{entry.date:%Y-%m-%d}</time>
          <span>{esc(entry.author)}</span>
        </div>
        <div class="term-list">{terms}</div>
      </header>
      {thumbnail}
      <div class="post-content">
        {body_content}
        {external}
      </div>
      <nav class="post-nav" aria-label="Post navigation">
        <span>{prev_link}</span>
        <span>{next_link}</span>
      </nav>
    </article>"""
    return render_layout(entry.title, body, entry.output_path, entry.excerpt or entry.content)


def render_index(entries: list[Entry]) -> str:
    posts = sorted([e for e in entries if e.kind == "post"], key=lambda e: e.date, reverse=True)
    pages = sorted([e for e in entries if e.kind == "page"], key=lambda e: e.title.lower())
    latest = posts[0].date.year if posts else dt.datetime.now().year
    oldest = posts[-1].date.year if posts else latest

    cards = []
    for entry in posts:
        summary = strip_tags(entry.excerpt or entry.content)[:260]
        terms = " ".join([t["name"] for t in entry.categories + entry.tags])
        cards.append(
            f"""          <article class="archive-item" data-search="{esc((entry.title + ' ' + summary + ' ' + terms).lower())}">
            <a href="{rel_url('index.html', entry.output_path)}">
              <time datetime="{entry.date:%Y-%m-%d}">{entry.date:%Y-%m-%d}</time>
              <h2>{esc(entry.title)}</h2>
              <p>{esc(summary)}</p>
            </a>
          </article>"""
        )

    page_links = "\n".join(
        f'          <a href="{rel_url("index.html", page.output_path)}">{esc(page.title)}</a>' for page in pages
    )

    body = f"""    <section class="archive-hero">
      <div>
        <p class="eyebrow">WordPress archive</p>
        <h1>kkolodziejczak</h1>
        <p>Preserved static archive of posts from kkolodziejczak.net, including original dates, categories, images, and code blocks.</p>
      </div>
      <dl class="stats">
        <div><dt>Posts</dt><dd>{len(posts)}</dd></div>
        <div><dt>Years</dt><dd>{oldest}-{latest}</dd></div>
        <div><dt>Pages</dt><dd>{len(pages)}</dd></div>
      </dl>
    </section>
    <section class="archive-tools" aria-label="Archive tools">
      <label for="archive-search">Search archive</label>
      <input id="archive-search" type="search" placeholder="Search posts, tags, categories">
    </section>
    <section class="archive-layout">
      <div>
        <h2 class="section-heading">Posts</h2>
        <div class="archive-list" id="archive-list">
{os.linesep.join(cards)}
        </div>
      </div>
      <aside>
        <h2 class="section-heading">Pages</h2>
        <div class="page-list">
{page_links}
        </div>
      </aside>
    </section>"""
    return render_layout("kkolodziejczak archive", body, "index.html")


def render_term_page(kind: str, slug: str, name: str, entries: list[Entry]) -> str:
    output_path = f"{kind}/{slug}/index.html"
    items = "\n".join(
        f"""        <article class="archive-item">
          <a href="{rel_url(output_path, entry.output_path)}">
            <time datetime="{entry.date:%Y-%m-%d}">{entry.date:%Y-%m-%d}</time>
            <h2>{esc(entry.title)}</h2>
          </a>
        </article>"""
        for entry in sorted(entries, key=lambda e: e.date, reverse=True)
    )
    label = "Category" if kind == "categories" else "Tag"
    body = f"""    <section class="term-page">
      <p class="eyebrow">{label}</p>
      <h1>{esc(name)}</h1>
      <div class="archive-list compact">
{items}
      </div>
    </section>"""
    return render_layout(f"{name} - kkolodziejczak archive", body, output_path)


def render_feed(entries: list[Entry]) -> str:
    posts = sorted([e for e in entries if e.kind == "post"], key=lambda e: e.date, reverse=True)[:25]
    items = []
    for entry in posts:
        pub = entry.date.strftime("%a, %d %b %Y %H:%M:%S +0000")
        items.append(
            f"""  <item>
    <title>{esc(entry.title)}</title>
    <link>{esc(canonical_url(entry.output_path))}</link>
    <guid>{esc(canonical_url(entry.output_path))}</guid>
    <pubDate>{pub}</pubDate>
    <description>{esc(strip_tags(entry.excerpt or entry.content)[:500])}</description>
  </item>"""
        )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>kkolodziejczak archive</title>
  <link>https://{SITE_HOST}/</link>
  <description>Static archive of kkolodziejczak.net</description>
{os.linesep.join(items)}
</channel>
</rss>
"""


def render_sitemap(entries: list[Entry]) -> str:
    urls = ["index.html", "feed.xml"] + [entry.output_path for entry in entries]
    body = "\n".join(f"  <url><loc>{esc(canonical_url(path))}</loc></url>" for path in urls)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{body}
</urlset>
"""


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = "\n".join(line.rstrip() for line in content.splitlines()) + "\n"
    path.write_text(normalized, encoding="utf-8", newline="\n")


def write_png_ico(source_path: Path, target_path: Path) -> None:
    if not source_path.exists():
        return

    data = source_path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n") or data[12:16] != b"IHDR":
        raise ValueError(f"{source_path} is not a PNG file.")

    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if width > 256 or height > 256:
        raise ValueError(f"{source_path} is too large for a favicon.ico entry.")

    target_path.write_bytes(
        b"\x00\x00\x01\x00\x01\x00"
        + bytes([width if width < 256 else 0, height if height < 256 else 0, 0, 0])
        + (1).to_bytes(2, "little")
        + (32).to_bytes(2, "little")
        + len(data).to_bytes(4, "little")
        + (22).to_bytes(4, "little")
        + data
    )


def generate_site(
    export_path: Path,
    output_dir: Path,
    download: bool,
    asset_timeout: int,
    asset_retries: int,
    max_assets: int | None,
) -> None:
    entries, _, download_urls = parse_export(export_path)
    permalink_map = {
        urllib.parse.urlparse(entry.source_url).path.rstrip("/") + "/": entry.output_path
        for entry in entries
        if entry.source_url
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    write_text(output_dir / ".nojekyll", "")
    write_png_ico(output_dir / FAVICON_PATH, output_dir / FAVICON_ICO_PATH)
    write_text(output_dir / "index.html", render_index(entries))
    write_text(output_dir / "feed.xml", render_feed(entries))
    write_text(output_dir / "sitemap.xml", render_sitemap(entries))

    search_index = []
    for entry in entries:
        write_text(output_dir / entry.output_path, render_entry(entry, entries, permalink_map))
        search_index.append(
            {
                "title": entry.title,
                "url": entry.output_path.replace("index.html", ""),
                "date": entry.date.strftime("%Y-%m-%d"),
                "kind": entry.kind,
                "categories": [c["name"] for c in entry.categories],
                "tags": [t["name"] for t in entry.tags],
            }
        )

    terms: dict[tuple[str, str], tuple[str, list[Entry]]] = {}
    for entry in entries:
        for category in entry.categories:
            key = ("categories", category["slug"])
            terms.setdefault(key, (category["name"], []) )[1].append(entry)
        for tag in entry.tags:
            key = ("tags", tag["slug"])
            terms.setdefault(key, (tag["name"], []) )[1].append(entry)

    for (kind, slug), (name, term_entries) in terms.items():
        write_text(output_dir / kind / slug / "index.html", render_term_page(kind, slug, name, term_entries))

    write_text(output_dir / "assets" / "search-index.json", json.dumps(search_index, ensure_ascii=False, indent=2))

    if download:
        print(
            f"Attempting to download up to {max_assets or len(download_urls)} media URLs "
            f"with timeout={asset_timeout}s retries={asset_retries}.",
            flush=True,
        )
        downloaded, failed = download_assets(
            download_urls,
            output_dir,
            retries=asset_retries,
            timeout=asset_timeout,
            max_assets=max_assets,
        )
        print(f"Downloaded {downloaded} media files.")
        if failed:
            failure_path = output_dir / "assets" / "download-failures.txt"
            write_text(failure_path, "\n".join(failed) + "\n")
            print(f"Failed to download {len(failed)} media files. See {failure_path}.")
        else:
            failure_path = output_dir / "assets" / "download-failures.txt"
            if failure_path.exists():
                failure_path.unlink()

    post_count = sum(1 for entry in entries if entry.kind == "post")
    page_count = sum(1 for entry in entries if entry.kind == "page")
    print(f"Generated {post_count} posts and {page_count} pages in {output_dir}.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate WordPress WXR export to a static GitHub Pages archive.")
    parser.add_argument("export", type=Path, help="Path to the WordPress XML export.")
    parser.add_argument("--output", type=Path, default=Path("."), help="Output directory; defaults to repository root.")
    parser.add_argument("--download-assets", action="store_true", help="Download WordPress media into assets/wp-content.")
    parser.add_argument("--asset-timeout", type=int, default=8, help="Per-asset download timeout in seconds.")
    parser.add_argument("--asset-retries", type=int, default=0, help="Retry count per asset after the first attempt.")
    parser.add_argument("--max-assets", type=int, default=None, help="Download only the first N media URLs.")
    args = parser.parse_args()

    generate_site(
        args.export.resolve(),
        args.output.resolve(),
        args.download_assets,
        args.asset_timeout,
        args.asset_retries,
        args.max_assets,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
