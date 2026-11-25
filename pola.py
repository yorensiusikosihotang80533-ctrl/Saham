import html, os, sys, time
from datetime import datetime, timezone, timedelta
import numpy as np, pandas as pd
import yfinance as yf

# ================= PENGATURAN (boleh diubah) =================
INTERVAL = "1h"   # "1h" = data sekitar 2 tahun (disarankan). "15m" = lebih rinci tapi cuma sekitar 60 hari
BIAYA = 0.004     # total fee beli + jual (0,4%). Sesuaikan sama sekuritas lu
MIN_HARI = 60     # saham dengan data kurang dari ini dilewati
MIN_UJI = 30      # minimal jumlah hari ujian biar sebuah pola boleh dinyatakan lolos
T_MIN = 2.0       # seberapa ketat syarat di data belajar (makin besar makin ketat)
JUMLAH_TOP = 30   # berapa pola yang ditampilin di tabel utama
# =============================================================

PERIODE = {"1h": "700d", "30m": "59d", "15m": "59d", "5m": "59d"}

def baca_daftar():
    kode = []
    with open("saham.txt") as fh:
        for baris in fh:
            baris = baris.split("#")[0]
            for k in baris.replace(";", ",").replace(" ", ",").split(","):
                k = k.strip().upper().replace(".JK", "")
                if k and k not in kode:
                    kode.append(k)
    return kode

def ambil(kode):
    for _ in range(3):
        try:
            df = yf.Ticker(kode + ".JK").history(period=PERIODE[INTERVAL], interval=INTERVAL, auto_adjust=True)
            if df is not None and len(df) > 0:
                if df.index.tz is not None:
                    df.index = df.index.tz_convert("Asia/Jakarta").tz_localize(None)
                df = df[["Open", "Close"]].dropna()
                return df[(df["Open"] > 0) & (df["Close"] > 0)]
        except Exception:
            pass
        time.sleep(3)
    return None

def susun(df):
    """Ubah data per jam jadi tabel: satu baris per hari, satu kolom per jam."""
    t = df.assign(hari=df.index.normalize(), slot=df.index.strftime("%H:%M"))
    buka = pd.pivot_table(t, index="hari", columns="slot", values="Open", aggfunc="first")
    tutup = pd.pivot_table(t, index="hari", columns="slot", values="Close", aggfunc="last")
    pakai = [s for s in buka.columns if buka[s].notna().mean() >= 0.6]
    if len(pakai) < 2:
        return None
    titik = buka[pakai].copy()
    titik["tutup"] = tutup.ffill(axis=1).iloc[:, -1]
    profil = []
    for s in pakai:
        g = (tutup[s] / buka[s] - 1).dropna()
        if len(g) > 0:
            profil.append({"slot": s, "rata": float(g.mean()), "naik": float((g > 0).mean()), "n": len(g)})
    return titik, profil

def statistik(r):
    r = r.replace([np.inf, -np.inf], np.nan).dropna()
    n = len(r)
    if n < 2:
        return None
    sd = r.std()
    return {"n": n, "rata": float(r.mean()), "menang": float((r > 0).mean()),
            "t": float(r.mean() / (sd / np.sqrt(n))) if sd > 0 else 0.0}

def nilai(jenis, beli, jual, r, batas):
    latih, uji = statistik(r.iloc[:batas]), statistik(r.iloc[batas:])
    if latih is None or uji is None:
        return None
    lulus = (latih["rata"] > 0 and latih["t"] >= T_MIN and uji["rata"] > 0
             and uji["menang"] > 0.5 and uji["n"] >= MIN_UJI)
    return {"jenis": jenis, "beli": beli, "jual": jual, "latih": latih, "uji": uji,
            "lulus": lulus, "skor": min(latih["rata"], uji["rata"])}

def cari(titik):
    """Coba semua kombinasi jam beli dan jam jual. Hasil sudah dipotong biaya."""
    nama = list(titik.columns)
    batas = int(len(titik) * 0.7)
    besok = titik.shift(-1)
    hasil = []
    for i, a in enumerate(nama):
        for j, b in enumerate(nama):
            if j > i:
                hasil.append(nilai("Hari yang sama", a, b, titik[b] / titik[a] - 1 - BIAYA, batas))
            hasil.append(nilai("Menginap", a, "besok " + b, besok[b] / titik[a] - 1 - BIAYA, batas))
    return [h for h in hasil if h]

# ---------- BIKIN HALAMAN ----------
def e(x):
    return html.escape(str(x))

def pct(x):
    return f"{x * 100:+.2f}%"

def baris_pola(kode, h, pakai_tautan):
    nama = f"<a href='#{kode}'><b>{kode}</b></a>" if pakai_tautan else ("✅" if h["lulus"] else "✗")
    return (f"<tr><td>{nama}</td><td>{h['jenis']}</td><td>{e(h['beli'])}</td><td>{e(h['jual'])}</td>"
            f"<td>{pct(h['latih']['rata'])}</td><td>{pct(h['uji']['rata'])}</td>"
            f"<td>{h['uji']['menang'] * 100:.0f}%</td><td>{h['uji']['n']}</td></tr>")

KEPALA_POLA = ("<tr><th>{}</th><th>Jenis</th><th>Beli</th><th>Jual</th><th>Rata-rata (belajar)</th>"
               "<th>Rata-rata (ujian)</th><th>Menang (ujian)</th><th>Hari ujian</th></tr>")

CSS = """
body{font-family:system-ui,sans-serif;max-width:1000px;margin:0 auto;padding:16px;color:#1a1a1a;background:#fafafa;line-height:1.5}
h1{font-size:1.5rem;margin-bottom:0} h2{font-size:1.15rem;margin-top:32px;border-bottom:2px solid #ddd;padding-bottom:4px}
.info{color:#555;font-size:.9rem} .catatan{background:#fff3cd;padding:8px 12px;border-radius:8px;font-size:.9rem}
.geser{overflow-x:auto} table{border-collapse:collapse;width:100%;background:#fff;font-size:.9rem;margin:8px 0}
th,td{padding:6px 10px;border-bottom:1px solid #e5e5e5;text-align:right;white-space:nowrap}
th:nth-child(-n+4),td:nth-child(-n+4){text-align:left} th{background:#f0f0f0}
details{background:#fff;border:1px solid #ddd;border-radius:8px;padding:10px 14px;margin:8px 0}
summary{cursor:pointer} a{color:#0b5cad;text-decoration:none} footer{margin-top:32px;color:#777;font-size:.8rem}
"""

JS = """
function buka(){var el=document.getElementById(location.hash.slice(1));
  if(el&&el.tagName==='DETAILS'){el.open=true;el.scrollIntoView();}}
window.addEventListener('hashchange',buka);buka();
"""

def bikin_halaman(semua, gagal, total_kombinasi):
    wib = datetime.now(timezone(timedelta(hours=7)))
    awal = min(s["awal"] for s in semua)
    akhir = max(s["akhir"] for s in semua)
    total_lulus = sum(len(s["lulus"]) for s in semua)
    b = []
    b.append("<!doctype html><html lang='id'><head><meta charset='utf-8'>")
    b.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    b.append("<title>Pola Jam-jaman Saham</title><style>" + CSS + "</style></head><body>")
    b.append("<h1>⏱ Pola Jam-jaman Saham</h1>")
    b.append("<p><a href='index.html'>← Kembali ke ringkasan harian</a></p>")
    b.append(f"<p class='info'>Data per {INTERVAL}, {awal:%d %b %Y} sampai {akhir:%d %b %Y} · "
             f"diperbarui {wib:%d %b %Y %H:%M} WIB · {len(semua)} saham</p>")
    b.append(f"<p class='catatan'>Dari <b>{total_kombinasi:,}</b> kombinasi jam beli dan jam jual yang dicoba, "
             f"<b>{total_lulus}</b> lolos ujian. Semua angka sudah dipotong biaya {BIAYA * 100:.1f}% per transaksi bolak-balik. "
             "Karena yang dicoba ribuan, sebagian yang lolos pasti cuma kebetulan. Anggap ini daftar kandidat buat dipantau, bukan kepastian.</p>".replace(",", ".", 1))
    if gagal:
        b.append(f"<p class='info'>Dilewati (data gagal diambil atau terlalu sedikit): {e(', '.join(gagal))}</p>")

    # --- Pola terbaik per saham ---
    b.append("<h2>Pola paling konsisten</h2>")
    terbaik = sorted([(s["kode"], s["lulus"][0]) for s in semua if s["lulus"]],
                     key=lambda x: x[1]["skor"], reverse=True)[:JUMLAH_TOP]
    if terbaik:
        b.append("<p class='info'>Satu pola terbaik per saham. Jam beli dan jual = harga di awal jam itu, "
                 "\"tutup\" = harga penutupan.</p>")
        b.append("<div class='geser'><table>" + KEPALA_POLA.format("Kode"))
        for kode, h in terbaik:
            b.append(baris_pola(kode, h, True))
        b.append("</table></div>")
    else:
        b.append("<p>Gak ada satu pun pola yang lolos ujian setelah dipotong biaya. "
                 "Itu juga informasi: pola jam-jaman di saham-saham ini gak cukup kuat buat dimakan.</p>")

    # --- Detail per saham ---
    b.append("<h2>Detail per saham</h2><p class='info'>Klik buat lihat gerak rata-rata tiap jam dan 5 pola teratasnya.</p>")
    for s in sorted(semua, key=lambda s: (len(s["lulus"]) == 0, s["kode"])):
        tanda = f"✅ {len(s['lulus'])} pola lolos" if s["lulus"] else "tidak ada pola yang lolos"
        b.append(f"<details id='{s['kode']}'><summary><b>{s['kode']}</b> · {tanda} · {s['hari']} hari data</summary>")
        b.append("<p><b>Gerak rata-rata tiap jam</b> (belum dipotong biaya)</p>")
        b.append("<div class='geser'><table><tr><th>Jam</th><th>Rata-rata gerak</th><th>Hari naik</th><th>Jumlah hari</th></tr>")
        for p in s["profil"]:
            b.append(f"<tr><td>{p['slot']}</td><td>{pct(p['rata'])}</td><td>{p['naik'] * 100:.0f}%</td><td>{p['n']}</td></tr>")
        b.append("</table></div>")
        b.append("<p><b>5 pola teratas</b> (sudah dipotong biaya)</p>")
        b.append("<div class='geser'><table>" + KEPALA_POLA.format("Lolos"))
        for h in s["pola"][:5]:
            b.append(baris_pola(s["kode"], h, False))
        b.append("</table></div></details>")

    b.append("<footer>Bukan saran investasi. Pola masa lalu bisa hilang kapan saja. "
             "Data dari Yahoo Finance, bisa telat atau tidak lengkap.</footer>")
    b.append("<script>" + JS + "</script></body></html>")
    return "\n".join(b)

def pasang_tautan():
    """Tambahin tautan ke halaman pola di halaman ringkasan harian."""
    jalur = "docs/index.html"
    if not os.path.exists(jalur):
        return
    with open(jalur, encoding="utf-8") as fh:
        isi = fh.read()
    if "pola.html" in isi:
        return
    isi = isi.replace("</h1>", "</h1><p><a href='pola.html'>⏱ Lihat pola jam-jaman →</a></p>", 1)
    with open(jalur, "w", encoding="utf-8") as fh:
        fh.write(isi)

# ---------- JALANKAN ----------
def utama():
    daftar = baca_daftar()
    semua, gagal, total_kombinasi = [], [], 0
    for kode in daftar:
        print("Pola", kode)
        df = ambil(kode)
        s = susun(df) if df is not None and len(df) > 0 else None
        if s is None or len(s[0]) < MIN_HARI:
            gagal.append(kode)
            continue
        titik, profil = s
        pola = cari(titik)
        pola.sort(key=lambda h: h["skor"], reverse=True)
        total_kombinasi += len(pola)
        semua.append({"kode": kode, "hari": len(titik), "awal": titik.index[0], "akhir": titik.index[-1],
                      "profil": profil, "pola": pola, "lulus": [h for h in pola if h["lulus"]]})
        time.sleep(0.3)
    if not semua:
        print("Semua saham gagal diambil. Halaman pola lama dibiarkan.")
        sys.exit(1)
    os.makedirs("docs", exist_ok=True)
    with open("docs/pola.html", "w", encoding="utf-8") as fh:
        fh.write(bikin_halaman(semua, gagal, total_kombinasi))
    pasang_tautan()
    print("Selesai:", len(semua), "saham,", len(gagal), "dilewati")

utama()
