# -*- coding: utf-8 -*-
"""
blog_pipeline.py — Motor SEO automático para Revaliu B2B
Flujo: RSS feeds (BanRep/Superfinanciera/Portafolio) → Gemini → HTML → docs/blog/
Frecuencia: 2-3 artículos semanales en piloto automático.
"""
from __future__ import annotations
import os, re, json, sqlite3, hashlib, time, textwrap
from datetime import datetime
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / "radar_nichos" / ".env")

GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY", "")
WHATSAPP_NUMBER = "+573184322874"
SITE_URL        = "https://revaliu.com"
DB_PATH         = Path(__file__).parent / "db" / "revaliu_b2b.db"
DOCS_BLOG       = Path(__file__).parent / "docs" / "blog"
DOCS_BLOG.mkdir(parents=True, exist_ok=True)

# ── RSS Feeds ──────────────────────────────────────────────────────────────────

RSS_FEEDS = [
    {
        "nombre": "Banco de la República",
        "url":    "https://www.banrep.gov.co/sites/default/files/rss/comunicados.xml",
        "tema":   "macroeconomía Colombia, tasas de interés, política monetaria",
    },
    {
        "nombre": "Superfinanciera",
        "url":    "https://www.superfinanciera.gov.co/inicio/rss/comunicados-y-conceptos-10084513",
        "tema":   "regulación financiera Colombia, sector solidario, cooperativas",
    },
    {
        "nombre": "Portafolio",
        "url":    "https://www.portafolio.co/rss/economia.xml",
        "tema":   "economía colombiana, empresas, finanzas corporativas",
    },
    {
        "nombre": "La República",
        "url":    "https://www.larepublica.co/rss/economia",
        "tema":   "economía, banca, inversión Colombia",
    },
]

# ── DB Setup ───────────────────────────────────────────────────────────────────

def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS articles (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        slug            TEXT    UNIQUE NOT NULL,
        titulo          TEXT    NOT NULL,
        meta_description TEXT,
        contenido_html  TEXT,
        keyword_seo     TEXT,
        fuente_url      TEXT,
        fuente_titulo   TEXT,
        fuente_nombre   TEXT,
        publicado_en    TEXT,
        estado          TEXT    DEFAULT 'DRAFT'
    );

    CREATE TABLE IF NOT EXISTS rss_items (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        feed_url    TEXT,
        item_hash   TEXT    UNIQUE,
        titulo      TEXT,
        url         TEXT,
        resumen     TEXT,
        fecha       TEXT,
        procesado   INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS leads_b2b (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre      TEXT,
        empresa     TEXT,
        email       TEXT,
        telefono    TEXT,
        vertical    TEXT,
        monto_cop   REAL,
        mensaje     TEXT,
        origen_url  TEXT,
        timestamp   TEXT    DEFAULT (datetime('now')),
        estado      TEXT    DEFAULT 'NUEVO'
    );
    """)
    con.commit()
    con.close()


# ── RSS Fetcher ────────────────────────────────────────────────────────────────

def fetch_rss(feed: dict) -> list[dict]:
    """Descarga y parsea un feed RSS. Retorna lista de items nuevos."""
    try:
        resp = requests.get(feed["url"], timeout=12,
                            headers={"User-Agent": "Revaliu-Bot/1.0"})
        if resp.status_code != 200:
            print(f"  [RSS] {feed['nombre']}: HTTP {resp.status_code}")
            return []
        xml = resp.text

        # Parseo simple sin librería externa
        items = []
        for block in re.findall(r"<item>(.*?)</item>", xml, re.DOTALL):
            titulo  = _extract_tag(block, "title")
            url     = _extract_tag(block, "link") or _extract_tag(block, "guid")
            resumen = re.sub(r"<[^>]+>", "", _extract_tag(block, "description") or "")[:600]
            fecha   = _extract_tag(block, "pubDate") or datetime.now().isoformat()
            if not titulo:
                continue
            items.append({"titulo": titulo, "url": url, "resumen": resumen,
                          "fecha": fecha, "feed": feed})

        print(f"  [RSS] {feed['nombre']}: {len(items)} items")
        return items
    except Exception as e:
        print(f"  [RSS] {feed['nombre']} error: {e}")
        return []


def _extract_tag(xml: str, tag: str) -> str:
    m = re.search(rf"<{tag}[^>]*><!\[CDATA\[(.*?)\]\]></{tag}>", xml, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", xml, re.DOTALL)
    return m.group(1).strip() if m else ""


def save_rss_items(items: list[dict]) -> list[dict]:
    """Guarda items nuevos en DB. Retorna solo los no procesados."""
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    nuevos = []
    for item in items:
        h = hashlib.md5((item["titulo"] + item.get("url", "")).encode()).hexdigest()
        try:
            cur.execute(
                "INSERT INTO rss_items (feed_url, item_hash, titulo, url, resumen, fecha) VALUES (?,?,?,?,?,?)",
                (item["feed"]["url"], h, item["titulo"], item["url"],
                 item["resumen"], item["fecha"])
            )
            nuevos.append(item)
        except sqlite3.IntegrityError:
            pass  # ya existe
    con.commit()
    con.close()
    return nuevos


# ── Gemini Article Generator ───────────────────────────────────────────────────

def generar_articulo_gemini(item: dict) -> dict | None:
    """
    Toma un item RSS y genera un artículo de análisis financiero completo.
    Retorna dict con titulo, slug, meta, contenido_html, keyword_seo.
    """
    if not GEMINI_API_KEY:
        print("  [Blog] Sin GEMINI_API_KEY — saltando generación")
        return None

    feed_tema = item["feed"]["tema"]
    prompt = f"""Eres un consultor financiero senior especializado en Colombia y LatAm.

Noticia fuente ({item['feed']['nombre']}):
Título: {item['titulo']}
Resumen: {item['resumen']}

Escribe un artículo de análisis financiero profesional en español para el blog de Revaliu (consultoría cuantitativa Colombia).

El artículo debe:
1. Analizar el impacto de la noticia en empresas, cooperativas o project managers en Colombia
2. Conectar con los servicios de Revaliu: modelos de riesgo, project finance, tableros financieros
3. Terminar SIEMPRE con este CTA exacto (no modificar):

---CTA---
<div class="article-cta">
  <h3>¿Cómo afecta esto a su empresa?</h3>
  <p>En Revaliu analizamos el impacto de estos cambios en su modelo de negocio específico. Agenda un diagnóstico gratuito de 30 minutos.</p>
  <a href="https://wa.me/573184322874?text=Hola%20Sergio%2C%20leí%20el%20artículo%20en%20Revaliu%20y%20quiero%20analizar%20el%20impacto%20en%20mi%20empresa" class="cta-wa-btn">💬 WhatsApp: +57 318 432 2874</a>
</div>
---FIN CTA---

Responde SOLO en este JSON (sin markdown, sin bloques de código):
{{
  "titulo_seo": "Título del artículo optimizado para SEO (máx 65 chars)",
  "keyword_seo": "palabra clave principal long-tail Colombia",
  "meta_description": "Meta description 155 chars máx",
  "h2_intro": "Primer subtítulo (H2)",
  "intro": "Párrafo introductorio 3-4 oraciones",
  "h2_impacto": "Segundo subtítulo: impacto en Colombia",
  "cuerpo_impacto": "2-3 párrafos de análisis del impacto en empresas colombianas",
  "h2_recomendacion": "Tercer subtítulo: qué hacer",
  "cuerpo_recomendacion": "2 párrafos con recomendación práctica",
  "cta_html": "<div class='article-cta'>...</div> (copiar el CTA de arriba exactamente)"
}}"""

    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}",
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.4,
                    "maxOutputTokens": 2048,
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            },
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"  [Gemini] HTTP {resp.status_code}: {resp.text[:200]}")
            return None

        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start == -1 or end == 0:
            print("  [Gemini] Sin JSON en respuesta")
            return None

        data = json.loads(text[start:end])
        return data
    except Exception as e:
        print(f"  [Gemini] Error: {e}")
        return None


# ── HTML Builder ───────────────────────────────────────────────────────────────

ARTICLE_CSS = """
<style>
:root{--navy:#0D1B2A;--gold:#C9A84C;--text:#2D3748;--text-light:#718096;--white:#fff;--gray:#F4F6F8}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;color:var(--text);background:var(--white);line-height:1.75}
nav{background:var(--navy);padding:0 5%;display:flex;justify-content:space-between;align-items:center;height:60px}
.logo{color:var(--gold);font-size:1.3rem;font-weight:700;letter-spacing:1px;text-decoration:none}
.nav-cta{background:var(--gold);color:var(--navy);padding:7px 16px;border-radius:5px;font-weight:600;font-size:.9rem;text-decoration:none}
.hero-article{background:var(--navy);padding:60px 5% 50px}
.breadcrumb{color:rgba(255,255,255,.5);font-size:.82rem;margin-bottom:16px}
.breadcrumb a{color:var(--gold);text-decoration:none}
h1{color:#fff;font-size:clamp(1.5rem,3.5vw,2.4rem);font-weight:700;line-height:1.25;max-width:780px}
.article-meta{color:rgba(255,255,255,.5);font-size:.85rem;margin-top:16px}
.article-wrap{max-width:780px;margin:50px auto;padding:0 5%}
h2{font-size:1.35rem;color:var(--navy);margin:36px 0 14px;font-weight:700}
p{margin-bottom:18px;font-size:1rem;color:var(--text);line-height:1.8}
.source-box{background:var(--gray);border-left:4px solid var(--gold);border-radius:0 8px 8px 0;padding:14px 18px;margin:24px 0;font-size:.88rem;color:var(--text-light)}
.article-cta{background:var(--navy);border-radius:12px;padding:32px;margin:40px 0;text-align:center}
.article-cta h3{color:#fff;font-size:1.2rem;margin-bottom:10px}
.article-cta p{color:rgba(255,255,255,.75);font-size:.95rem;margin-bottom:20px}
.cta-wa-btn{display:inline-flex;align-items:center;gap:8px;background:#25D366;color:#fff;padding:13px 28px;border-radius:8px;font-size:.95rem;font-weight:700;text-decoration:none}
.cta-wa-btn:hover{background:#1ebe5d}
footer{background:var(--navy);padding:32px 5%;text-align:center;margin-top:60px}
footer p{color:rgba(255,255,255,.5);font-size:.85rem}
footer a{color:var(--gold);text-decoration:none}
</style>
"""

def build_article_html(data: dict, item: dict, fecha: str) -> str:
    slug    = _slugify(data["titulo_seo"])
    cta_html = data.get("cta_html") or f"""
<div class="article-cta">
  <h3>¿Cómo afecta esto a su empresa?</h3>
  <p>En Revaliu analizamos el impacto de estos cambios en su modelo de negocio. Agenda un diagnóstico gratuito.</p>
  <a href="https://wa.me/573184322874?text=Hola+Sergio%2C+le%C3%AD+el+art%C3%ADculo+en+Revaliu+y+quiero+analizar+el+impacto+en+mi+empresa" class="cta-wa-btn">💬 WhatsApp: +57 318 432 2874</a>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{data['titulo_seo']} | Revaliu</title>
<meta name="description" content="{data['meta_description']}">
<meta name="keywords" content="{data['keyword_seo']}, consultoría financiera Colombia, Revaliu">
<link rel="canonical" href="{SITE_URL}/blog/{slug}/">
<script type="application/ld+json">
{{"@context":"https://schema.org","@type":"Article","headline":"{data['titulo_seo']}",
"author":{{"@type":"Organization","name":"Revaliu"}},"publisher":{{"@type":"Organization","name":"Revaliu","telephone":"+573184322874"}},
"datePublished":"{fecha}","description":"{data['meta_description']}"}}
</script>
{ARTICLE_CSS}
</head>
<body>
<nav>
  <a class="logo" href="/">REVALIU</a>
  <a href="https://wa.me/573184322874?text=Hola%20Sergio%2C%20lei%20el%20blog%20de%20Revaliu%20y%20quisiera%20agendar%20un%20diagnostico" target="_blank" class="nav-cta">Agendar Diagnóstico</a>
</nav>
<div class="hero-article">
  <div class="breadcrumb"><a href="/">Inicio</a> › <a href="/blog/">Blog</a> › Análisis</div>
  <h1>{data['titulo_seo']}</h1>
  <div class="article-meta">Revaliu · {fecha[:10]} · Fuente: {item['feed']['nombre']}</div>
</div>
<div class="article-wrap">
  <div class="source-box">📰 Basado en: <a href="{item.get('url','#')}" target="_blank" rel="nofollow">{item['titulo']}</a> — {item['feed']['nombre']}</div>
  <h2>{data['h2_intro']}</h2>
  <p>{data['intro']}</p>
  <h2>{data['h2_impacto']}</h2>
  {''.join(f'<p>{p.strip()}</p>' for p in data['cuerpo_impacto'].split('\n\n') if p.strip())}
  <h2>{data['h2_recomendacion']}</h2>
  {''.join(f'<p>{p.strip()}</p>' for p in data['cuerpo_recomendacion'].split('\n\n') if p.strip())}
  {cta_html}
</div>
<footer>
  <p>© 2026 Revaliu · <a href="/">Inicio</a> · <a href="/blog/">Blog</a> · <a href="mailto:srojas@revaliu.com">srojas@revaliu.com</a> · <a href="https://wa.me/573184322874">+57 318 432 2874</a></p>
</footer>
</body>
</html>"""


def _slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[áàä]", "a", text)
    text = re.sub(r"[éèë]", "e", text)
    text = re.sub(r"[íìï]", "i", text)
    text = re.sub(r"[óòö]", "o", text)
    text = re.sub(r"[úùü]", "u", text)
    text = re.sub(r"ñ", "n", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")[:60]


def save_article(slug: str, data: dict, html: str, item: dict, fecha: str):
    """Guarda artículo en DB y escribe el HTML en docs/blog/<slug>/index.html."""
    # Escribir HTML
    out_dir = DOCS_BLOG / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(html, encoding="utf-8")

    # Guardar en DB
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    try:
        cur.execute("""INSERT OR IGNORE INTO articles
            (slug, titulo, meta_description, contenido_html, keyword_seo,
             fuente_url, fuente_titulo, fuente_nombre, publicado_en, estado)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (slug, data["titulo_seo"], data["meta_description"], html,
             data["keyword_seo"], item.get("url",""), item["titulo"],
             item["feed"]["nombre"], fecha, "PUBLICADO")
        )
        # Marcar rss_item como procesado
        h = hashlib.md5((item["titulo"] + item.get("url","")).encode()).hexdigest()
        cur.execute("UPDATE rss_items SET procesado=1 WHERE item_hash=?", (h,))
        con.commit()
        print(f"  [Blog] ✅ Publicado: /blog/{slug}/")
    except sqlite3.IntegrityError:
        print(f"  [Blog] Ya existe: {slug}")
    finally:
        con.close()


# ── Blog Hub ───────────────────────────────────────────────────────────────────

def rebuild_blog_hub():
    """Regenera docs/blog/index.html con la lista de artículos publicados."""
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT slug, titulo, meta_description, keyword_seo, publicado_en FROM articles WHERE estado='PUBLICADO' ORDER BY publicado_en DESC LIMIT 50")
    arts = cur.fetchall()
    con.close()

    cards = ""
    for slug, titulo, meta, kw, fecha in arts:
        cards += f"""
    <a class="blog-card" href="/blog/{slug}/">
      <div class="blog-kw">{kw}</div>
      <h3>{titulo}</h3>
      <p>{meta[:120]}...</p>
      <div class="blog-meta">{fecha[:10]}</div>
    </a>"""

    if not cards:
        cards = "<p style='color:var(--text-light);text-align:center;padding:40px'>Los primeros artículos se publicarán pronto.</p>"

    hub_html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Blog de Análisis Financiero Colombia | Riesgo, Project Finance y Automatización | Revaliu</title>
<meta name="description" content="Análisis de riesgo cuantitativo, project finance y automatización financiera para empresas en Colombia. Publicaciones semanales gratuitas.">
<style>
:root{{--navy:#0D1B2A;--gold:#C9A84C;--white:#fff;--gray:#F4F6F8;--text:#2D3748;--text-light:#718096}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI',system-ui,sans-serif;color:var(--text);background:var(--white)}}
nav{{background:var(--navy);padding:0 5%;display:flex;justify-content:space-between;align-items:center;height:60px}}
.logo{{color:var(--gold);font-size:1.3rem;font-weight:700;text-decoration:none}}
.nav-cta{{background:var(--gold);color:var(--navy);padding:7px 16px;border-radius:5px;font-weight:600;font-size:.9rem;text-decoration:none}}
.hero{{background:var(--navy);padding:60px 5%;text-align:center}}
.hero h1{{color:#fff;font-size:clamp(1.6rem,3.5vw,2.5rem);margin-bottom:12px}}
.hero h1 span{{color:var(--gold)}}
.hero p{{color:rgba(255,255,255,.7);font-size:1.05rem}}
.section{{padding:60px 5%;max-width:1100px;margin:0 auto}}
.blog-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:24px;margin-top:16px}}
.blog-card{{border:1px solid #E2E8F0;border-radius:12px;padding:24px;text-decoration:none;color:inherit;transition:all .3s;display:block}}
.blog-card:hover{{border-color:var(--gold);box-shadow:0 6px 24px rgba(201,168,76,.12);transform:translateY(-3px)}}
.blog-kw{{color:var(--gold);font-size:.8rem;font-weight:600;letter-spacing:.5px;text-transform:uppercase;margin-bottom:10px}}
.blog-card h3{{color:var(--navy);font-size:1.05rem;line-height:1.4;margin-bottom:10px}}
.blog-card p{{color:var(--text-light);font-size:.88rem;line-height:1.6}}
.blog-meta{{color:var(--text-light);font-size:.8rem;margin-top:14px}}
.cta-sub{{background:var(--navy);padding:60px 5%;text-align:center}}
.cta-sub h2{{color:#fff;font-size:1.6rem;margin-bottom:10px}}
.cta-sub p{{color:rgba(255,255,255,.7);margin-bottom:24px}}
.btn-wa{{display:inline-flex;align-items:center;gap:8px;background:#25D366;color:#fff;padding:13px 28px;border-radius:8px;font-weight:700;text-decoration:none}}
footer{{background:var(--navy);padding:30px 5%;text-align:center}}
footer p{{color:rgba(255,255,255,.5);font-size:.85rem}}
footer a{{color:var(--gold);text-decoration:none}}
</style>
</head>
<body>
<nav><a class="logo" href="/">REVALIU</a>
<a href="https://wa.me/573184322874?text=Hola%20Sergio%2C%20leí%20el%20blog%20y%20quisiera%20agendar%20un%20diagnóstico" target="_blank" class="nav-cta">Agendar Diagnóstico</a></nav>
<div class="hero">
  <h1>Análisis Financiero <span>para Colombia</span></h1>
  <p>Riesgo cuantitativo · Project Finance · Automatización · 2-3 artículos por semana</p>
</div>
<div class="section">
  <div class="blog-grid">{cards}</div>
</div>
<div class="cta-sub">
  <h2>¿Quiere análisis a medida para su empresa?</h2>
  <p>Lo que publicamos en el blog es genérico. Lo que hacemos para nuestros clientes es específico para su caso.</p>
  <a href="https://wa.me/573184322874?text=Hola%20Sergio%2C%20leí%20el%20blog%20de%20Revaliu%20y%20quiero%20hablar%20de%20mi%20caso" target="_blank" class="btn-wa">💬 +57 318 432 2874</a>
</div>
<footer><p>© 2026 Revaliu · <a href="/">Inicio</a> · <a href="mailto:srojas@revaliu.com">srojas@revaliu.com</a></p></footer>
</body>
</html>"""
    (DOCS_BLOG / "index.html").write_text(hub_html, encoding="utf-8")
    print(f"  [Blog Hub] Regenerado con {len(arts)} artículos")


# ── Main Pipeline ──────────────────────────────────────────────────────────────

def run(max_articles: int = 3):
    """Corre el pipeline completo: RSS → Gemini → HTML → Blog."""
    print(f"\n{'='*55}")
    print(f"  BLOG PIPELINE — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*55}")

    init_db()
    publicados = 0

    for feed in RSS_FEEDS:
        if publicados >= max_articles:
            break
        print(f"\n[Feed] {feed['nombre']}")
        items = fetch_rss(feed)
        nuevos = save_rss_items(items)
        print(f"  Nuevos: {len(nuevos)}")

        for item in nuevos[:2]:
            if publicados >= max_articles:
                break
            print(f"  Generando artículo: {item['titulo'][:70]}...")
            data = generar_articulo_gemini(item)
            if not data:
                continue
            fecha = datetime.now().isoformat()
            slug  = _slugify(data["titulo_seo"])
            html  = build_article_html(data, item, fecha)
            save_article(slug, data, html, item, fecha)
            publicados += 1
            time.sleep(3)

    rebuild_blog_hub()
    print(f"\n  Blog pipeline completo: {publicados} artículos publicados")
    return publicados


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    n = run(max_articles=3)
    print(f"\nListo. {n} artículos nuevos en docs/blog/")
