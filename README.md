# kkolodziejczak static archive

This repository contains a static GitHub Pages archive migrated from the WordPress export `kkolodziejczak.WordPress.2026-07-01.xml`.

## What is generated

- Published WordPress posts are emitted under their original permalink paths, for example `2017/07/15/hello-world/index.html`.
- Published WordPress pages are emitted under root page paths, for example `about/index.html`.
- Original dates, authors, categories, tags, featured images, inline images, captions, and code blocks are preserved.
- WordPress media URLs are rewritten to local files under `assets/wp-content/uploads/`.
- `index.html`, `feed.xml`, `sitemap.xml`, category pages, and tag pages are generated for GitHub Pages.

## Regenerate from the WordPress export

Run this from the repository root:

```powershell
python tools/migrate_wordpress.py "C:\Users\Krzysiek\Desktop\kkolodziejczak.WordPress.2026-07-01.xml" --output . --download-assets
```

If you already have the media files and only want to regenerate HTML:

```powershell
python tools/migrate_wordpress.py "C:\Users\Krzysiek\Desktop\kkolodziejczak.WordPress.2026-07-01.xml" --output .
```

## Publish on GitHub Pages

In the GitHub repository settings, enable Pages from the repository root on the branch you publish. This archive is plain static HTML and uses `.nojekyll`, so it does not need a build step.
