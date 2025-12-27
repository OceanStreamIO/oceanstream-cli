# Building the Documentation Site

## Quick Start

```bash
# Install dependencies
pip install -r docs-requirements.txt

# Serve locally (with live reload)
cd docs-site
mkdocs serve

# Open http://127.0.0.1:8000 in browser
```

## Build Static Site

```bash
cd docs-site
mkdocs build

# Output in: site/
# Deploy to GitHub Pages, Netlify, etc.
```

## Deploy to GitHub Pages

```bash
cd docs-site
mkdocs gh-deploy

# Automatically builds and pushes to gh-pages branch
# Site will be at: https://oceanstreamio.github.io/oceanstream-newcli/
```

## Using with Docker

```bash
# Serve with Docker
docker run --rm -it -p 8000:8000 -v ${PWD}/docs-site:/docs squidfunk/mkdocs-material

# Build with Docker
docker run --rm -it -v ${PWD}/docs-site:/docs squidfunk/mkdocs-material build
```

## Directory Structure

The docs-site/ folder is self-contained and can be built independently from the main project.

All markdown files reference each other relatively, so the site can be browsed as plain files OR built with MkDocs.
