#!/usr/bin/env python3
"""
Scrapes Télam Malvinas photo galleries from archive.org and builds a static website.
"""

import json
import os
import re
import time
import urllib.parse
from pathlib import Path

import requests

BASE_DIR = Path(__file__).parent
IMAGES_DIR = BASE_DIR / "docs" / "images"
SITE_DIR = BASE_DIR / "docs"

SESSION = requests.Session()
SESSION.headers["User-Agent"] = (
    "Mozilla/5.0 (compatible; archive-preservation-bot/1.0)"
)

GALLERIES = [
    ("fotos_malvinas_desembarco",        "El desembarco"),
    ("fotos_malvinas_dia_después_prensa", "Operación prensa"),
    ("fotos_malvinas_Menendez_gobernador","Menéndez gobernador"),
    ("fotos_malvinas_primer_bombardeo",   "El primer bombardeo"),
    ("fotos_malvinas_vida_cotidiana",     "Vida cotidiana"),
    ("fotos_malvinas_soldados",           "Los soldados"),
    ("fotos_malvinas_trincheras",         "Las trincheras"),
    ("fotos_malvinas_Irizar",             "El Irizar"),
    ("fotos_malvinas_mas_conocidas",      "Las más conocidas"),
    ("fotos_malvinas_corresponsales",     "Los corresponsales"),
]

# archive.org timestamp — verified from the page's own Wayback header
TIMESTAMP = "20220705224108"


def archive_url(slug: str) -> str:
    encoded = urllib.parse.quote(slug, safe="")
    return f"https://web.archive.org/web/{TIMESTAMP}/https://www.telam.com.ar/{slug}"


def fetch_page(url: str) -> str:
    print(f"  Fetching {url}")
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def parse_gallery(html: str, slug: str) -> dict:
    """Extract title, description, and image URLs from a gallery page."""

    # Title: first meaningful h2 or h3 that isn't the site name
    titles = re.findall(r"<h[23][^>]*>(.*?)</h[23]>", html, re.DOTALL)
    title = ""
    for t in titles:
        clean = re.sub(r"<[^>]+>", "", t).strip()
        if clean and "Télam" not in clean and len(clean) < 120:
            title = clean
            break

    # Description: longest paragraph that looks like editorial content
    paras = re.findall(r"<p[^>]*>(.*?)</p>", html, re.DOTALL)
    description = ""
    for p in paras:
        clean = re.sub(r"<[^>]+>", "", p).strip()
        if len(clean) > 60 and "Seguinos" not in clean and "@" not in clean:
            description = clean
            break

    # Image URLs: all advf/imagenes entries (deduplicated, order preserved)
    raw_imgs = re.findall(
        r'src="(https?://[^"]*telam\.com\.ar/advf/imagenes[^"]*\.(?:jpg|jpeg|png|webp))"',
        html,
        re.IGNORECASE,
    )
    # Also catch without extension (telam served extensionless PNGs)
    raw_imgs += re.findall(
        r'src="(https?://[^"]*telam\.com\.ar/advf/imagenes/[0-9a-f/\.]+)"',
        html,
        re.IGNORECASE,
    )

    seen = set()
    images = []
    for url in raw_imgs:
        # Normalise: strip archive.org wrapper if present, then re-wrap cleanly
        # archive.org im_ URL pattern: /web/TSim_/https://www.telam...
        m = re.search(r"/web/\d+im_/(https?://.+)", url)
        original = m.group(1) if m else url
        if original not in seen:
            seen.add(original)
            # Build clean archive.org direct-image URL
            archive_img = f"https://web.archive.org/web/{TIMESTAMP}im_/{original}"
            images.append({"original": original, "archive": archive_img})

    return {"slug": slug, "title": title, "description": description, "images": images}


def download_image(url: str, dest: Path) -> bool:
    if dest.exists():
        print(f"    skip (exists): {dest.name}")
        return True
    try:
        r = SESSION.get(url, timeout=60, stream=True)
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)
        size_kb = dest.stat().st_size // 1024
        print(f"    saved {dest.name} ({size_kb} KB)")
        time.sleep(0.5)   # be polite to archive.org
        return True
    except Exception as e:
        print(f"    ERROR downloading {url}: {e}")
        return False


def slug_to_filename(original_url: str, idx: int) -> str:
    # Use the hash-like filename from the URL, fall back to index
    m = re.search(r"/([0-9a-f]{10,})\.", original_url)
    if m:
        return m.group(1) + ".png"
    m = re.search(r"/([0-9a-f]{10,})$", original_url)
    if m:
        return m.group(1) + ".png"
    return f"img_{idx:03d}.png"


def main():
    SITE_DIR.mkdir(exist_ok=True)

    all_galleries = []

    for slug, fallback_title in GALLERIES:
        print(f"\n=== {fallback_title} ({slug}) ===")
        url = archive_url(slug)

        try:
            html = fetch_page(url)
        except Exception as e:
            print(f"  ERROR fetching page: {e}")
            continue

        gallery = parse_gallery(html, slug)
        if not gallery["title"]:
            gallery["title"] = fallback_title

        print(f"  Title: {gallery['title']}")
        print(f"  Description: {gallery['description'][:80]}...")
        print(f"  Images found: {len(gallery['images'])}")

        gallery_img_dir = IMAGES_DIR / slug
        gallery_img_dir.mkdir(parents=True, exist_ok=True)

        local_images = []
        for idx, img in enumerate(gallery["images"]):
            filename = slug_to_filename(img["original"], idx)
            dest = gallery_img_dir / filename
            ok = download_image(img["archive"], dest)
            if ok:
                local_images.append({
                    "file": f"images/{slug}/{filename}",
                    "original_url": img["original"],
                })

        gallery["local_images"] = local_images
        all_galleries.append(gallery)
        time.sleep(1)

    # Save metadata
    data_path = SITE_DIR / "data.json"
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(all_galleries, f, ensure_ascii=False, indent=2)
    print(f"\nSaved metadata to {data_path}")

    # Generate site
    build_site(all_galleries)
    print(f"Site built at {SITE_DIR}/index.html")


def build_site(galleries: list):
    total = sum(len(g["local_images"]) for g in galleries)

    nav_items = "".join(
        f'<li><a href="#{g["slug"]}">{g["title"]}</a></li>'
        for g in galleries
    )

    sections = ""
    for i, g in enumerate(galleries):
        imgs_html = ""
        for img in g["local_images"]:
            imgs_html += (
                f'<figure class="photo">'
                f'<a href="{img["file"]}" class="lightbox-trigger"'
                f' data-src="{img["file"]}" data-gallery="{g["title"]}">'
                f'<img src="{img["file"]}" loading="lazy" alt="">'
                f'</a>'
                f'</figure>\n'
            )

        desc_html = f'<p class="gallery-desc">{g["description"]}</p>' if g["description"] else ""
        num = f"{i + 1:02d}"

        sections += f"""
<section id="{g['slug']}" class="gallery-section">
  <div class="gallery-header">
    <span class="gallery-num">{num}</span>
    <h2>{g['title']}</h2>
  </div>
  {desc_html}
  <div class="photo-grid">
    {imgs_html}
  </div>
</section>
"""

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fotos Malvinas — Archivo Télam</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,600;1,400&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --bg: #0a0a0a;
    --surface: #131313;
    --border: #222;
    --text: #d8d0c4;
    --muted: #666;
    --muted-light: #888;
    --accent: #b8975a;
    --nav-h: 44px;
    --font-display: 'Cormorant Garamond', Georgia, serif;
    --font-body: system-ui, sans-serif;
  }}

  html {{ scroll-behavior: smooth; }}

  /* keep this in sync with actual nav height via JS */
  :root {{ --nav-offset: 80px; }}

  body {{
    background: var(--bg);
    color: var(--text);
    font-family: var(--font-body);
    line-height: 1.6;
    border-top: 2px solid var(--accent);
  }}

  /* ── Header ── */
  header {{
    padding: 4rem 2rem 3rem;
    text-align: center;
    border-bottom: 1px solid var(--border);
    position: relative;
  }}
  header h1 {{
    font-family: var(--font-display);
    font-size: clamp(2.4rem, 6vw, 4.4rem);
    font-weight: 400;
    letter-spacing: 0.06em;
    color: var(--text);
    line-height: 1.1;
  }}
  header p.subtitle {{
    margin-top: 1rem;
    color: var(--muted-light);
    font-size: 0.78rem;
    letter-spacing: 0.18em;
    text-transform: uppercase;
  }}
  header p.count {{
    margin-top: 0.4rem;
    color: var(--muted);
    font-size: 0.78rem;
    letter-spacing: 0.06em;
  }}

  /* ── Nav ── */
  nav {{
    position: sticky;
    top: 0;
    z-index: 100;
    background: rgba(10, 10, 10, 0.97);
    backdrop-filter: blur(8px);
    border-bottom: 1px solid var(--border);
  }}
  nav ul {{
    list-style: none;
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 0;
    padding: 0.5rem 1rem;
  }}
  nav a {{
    display: block;
    padding: 0.3rem 0.8rem;
    color: var(--muted);
    text-decoration: none;
    font-size: 0.72rem;
    letter-spacing: 0.07em;
    text-transform: uppercase;
    transition: color 0.15s;
    white-space: nowrap;
    border-bottom: 1px solid transparent;
    line-height: 1.8;
  }}
  nav a:hover {{ color: var(--text); }}
  nav a.active {{ color: var(--accent); border-bottom-color: var(--accent); }}

  /* ── Gallery sections ── */
  .gallery-section {{
    max-width: 1440px;
    margin: 0 auto;
    padding: 4rem 2rem;
    border-bottom: 1px solid var(--border);
    scroll-margin-top: var(--nav-offset);
  }}
  .gallery-header {{
    display: flex;
    align-items: baseline;
    gap: 1.2rem;
    margin-bottom: 1.4rem;
  }}
  .gallery-num {{
    font-family: var(--font-display);
    font-size: 0.85rem;
    color: var(--muted);
    letter-spacing: 0.1em;
    flex-shrink: 0;
    padding-top: 0.15em;
  }}
  .gallery-section h2 {{
    font-family: var(--font-display);
    font-size: clamp(1.6rem, 3.5vw, 2.6rem);
    font-weight: 400;
    color: var(--text);
    line-height: 1.1;
  }}
  .gallery-desc {{
    max-width: 720px;
    color: var(--muted-light);
    font-size: 0.9rem;
    line-height: 1.8;
    margin-bottom: 2.5rem;
    padding-left: 1.2rem;
    border-left: 1px solid var(--border);
  }}

  /* ── Photo grid ── */
  .photo-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 4px;
  }}
  .photo {{
    aspect-ratio: 4/3;
    overflow: hidden;
    background: var(--surface);
    cursor: pointer;
  }}
  .photo img {{
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
    transition: transform 0.4s ease, opacity 0.3s ease;
    opacity: 0.82;
  }}
  .photo:hover img {{ transform: scale(1.03); opacity: 1; }}

  /* ── Lightbox ── */
  #lightbox {{
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.96);
    z-index: 200;
    align-items: center;
    justify-content: center;
  }}
  #lightbox.open {{ display: flex; }}
  #lightbox-img {{
    max-width: 92vw;
    max-height: 90vh;
    object-fit: contain;
  }}
  #lightbox-close {{
    position: fixed;
    top: 1.2rem;
    right: 1.4rem;
    background: none;
    border: none;
    color: var(--muted);
    font-size: 1.5rem;
    cursor: pointer;
    line-height: 1;
    letter-spacing: -1px;
    transition: color 0.15s;
    font-family: var(--font-body);
  }}
  #lightbox-close:hover {{ color: var(--text); }}
  #lightbox-prev, #lightbox-next {{
    position: fixed;
    top: 50%;
    transform: translateY(-50%);
    background: none;
    border: none;
    color: var(--muted);
    font-size: 2rem;
    cursor: pointer;
    padding: 1rem 1.4rem;
    transition: color 0.15s;
    font-family: var(--font-display);
    line-height: 1;
  }}
  #lightbox-prev {{ left: 0; }}
  #lightbox-next {{ right: 0; }}
  #lightbox-prev:hover, #lightbox-next:hover {{ color: var(--text); }}
  #lightbox-info {{
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.8rem 1.4rem;
    border-top: 1px solid var(--border);
    background: rgba(0,0,0,0.6);
  }}
  #lightbox-gallery {{
    font-family: var(--font-display);
    font-style: italic;
    color: var(--muted-light);
    font-size: 0.95rem;
    letter-spacing: 0.02em;
  }}
  #lightbox-counter {{
    color: var(--muted);
    font-size: 0.75rem;
    letter-spacing: 0.1em;
  }}

  /* ── Footer ── */
  footer {{
    text-align: center;
    padding: 3rem 1rem;
    color: var(--muted);
    font-size: 0.78rem;
    line-height: 2;
    letter-spacing: 0.04em;
  }}
  footer a {{ color: var(--muted); text-decoration: none; border-bottom: 1px solid var(--border); }}
  footer a:hover {{ color: var(--muted-light); }}

  @media (max-width: 600px) {{
    .photo-grid {{ grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 3px; }}
    .gallery-section {{ padding: 2.5rem 1rem; }}
  }}
</style>
</head>
<body>

<header>
  <h1>Fotos Malvinas</h1>
  <p class="subtitle">Archivo Télam &thinsp;·&thinsp; Guerra de Malvinas 1982</p>
  <p class="count">{total}&nbsp;fotografías &nbsp;·&nbsp; {len(galleries)}&nbsp;galerías</p>
</header>

<nav id="main-nav">
  <ul>
    {nav_items}
  </ul>
</nav>

<main>
{sections}
</main>

<footer>
  <p>Fotografías: Télam — Agencia Nacional de Noticias Argentina</p>
  <p>Fuente original: <a href="https://web.archive.org/web/20220705224108/https://www.telam.com.ar/fotos_malvinas" target="_blank">web.archive.org</a></p>
</footer>

<div id="lightbox">
  <button id="lightbox-close" aria-label="Cerrar">&#x2715;</button>
  <button id="lightbox-prev" aria-label="Anterior">&#x2039;</button>
  <button id="lightbox-next" aria-label="Siguiente">&#x203A;</button>
  <img id="lightbox-img" src="" alt="">
  <div id="lightbox-info">
    <span id="lightbox-gallery"></span>
    <span id="lightbox-counter"></span>
  </div>
</div>

<script>
(function () {{
  const lb       = document.getElementById('lightbox');
  const lbImg    = document.getElementById('lightbox-img');
  const lbGal    = document.getElementById('lightbox-gallery');
  const lbCount  = document.getElementById('lightbox-counter');
  const triggers = Array.from(document.querySelectorAll('.lightbox-trigger'));
  let cur = 0;

  function openLb(idx) {{
    cur = idx;
    const el = triggers[idx];
    lbImg.src = el.dataset.src;
    lbGal.textContent = el.dataset.gallery || '';
    lbCount.textContent = (idx + 1) + ' / ' + triggers.length;
    lb.classList.add('open');
    document.body.style.overflow = 'hidden';
  }}

  function closeLb() {{
    lb.classList.remove('open');
    lbImg.src = '';
    document.body.style.overflow = '';
  }}

  function prevLb() {{ openLb((cur - 1 + triggers.length) % triggers.length); }}
  function nextLb() {{ openLb((cur + 1) % triggers.length); }}

  triggers.forEach((el, i) => el.addEventListener('click', e => {{ e.preventDefault(); openLb(i); }}));
  document.getElementById('lightbox-close').addEventListener('click', closeLb);
  document.getElementById('lightbox-prev').addEventListener('click', prevLb);
  document.getElementById('lightbox-next').addEventListener('click', nextLb);
  lb.addEventListener('click', e => {{ if (e.target === lb) closeLb(); }});
  document.addEventListener('keydown', e => {{
    if (!lb.classList.contains('open')) return;
    if (e.key === 'Escape') closeLb();
    if (e.key === 'ArrowLeft') prevLb();
    if (e.key === 'ArrowRight') nextLb();
  }});

  /* ── Sync scroll-margin-top with actual sticky nav height ── */
  const nav = document.getElementById('main-nav');
  function syncNavOffset() {{
    document.documentElement.style.setProperty('--nav-offset', nav.offsetHeight + 'px');
  }}
  syncNavOffset();
  window.addEventListener('resize', syncNavOffset);

  /* ── Active nav highlight via IntersectionObserver ── */
  const navLinks = Object.fromEntries(
    Array.from(document.querySelectorAll('nav a')).map(a => [a.getAttribute('href').slice(1), a])
  );
  const observer = new IntersectionObserver(entries => {{
    entries.forEach(e => {{
      if (e.isIntersecting && navLinks[e.target.id]) {{
        Object.values(navLinks).forEach(a => a.classList.remove('active'));
        navLinks[e.target.id].classList.add('active');
      }}
    }});
  }}, {{ rootMargin: '-20% 0px -70% 0px' }});
  document.querySelectorAll('.gallery-section').forEach(s => observer.observe(s));
}})();
</script>
</body>
</html>
"""

    out = SITE_DIR / "index.html"
    out.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
