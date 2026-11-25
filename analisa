import html, json, os, sys, time
from datetime import datetime, timezone, timedelta
import numpy as np, pandas as pd
import yfinance as yf, plotly.graph_objects as go

# ================= PENGATURAN (boleh diubah) =================
MODAL = 10_000_000   # modal lu dalam rupiah
RISIKO = 1           # maksimal rugi per transaksi, % dari modal
MIN_LIKUID = 5       # minimal nilai transaksi harian, miliar rupiah
JUMLAH_TOP = 5       # berapa saham yang ditampilin di bagian "layak beli"
BIAYA = 0.002        # perkiraan fee + selisih harga tiap transaksi (0,2%)
STOP_ATR = 2         # stop loss = harga - 2x gerak harian rata-rata
TARGET_ATR = 3       # target    = harga + 3x gerak harian rata-rata
VOLUME_RAMAI = 1.5   # volume disebut ramai kalau 1,5x rata-rata 20 hari
# =============================================================

# ---------- AMBIL DATA ----------
def ambil_harga(kode):
    for _ in range(3):
        try:
            df = yf.Ticker(kode + ".JK").history(period="5y", auto_adjust=True)
            if df is not None and len(df) >= 300:
                df.index = pd.to_datetime(df.index).tz_localize(None)
                return df[["High", "Low", "Close", "Volume"]].dropna()
        except Exception:
            pass
        time.sleep(3)
    return None

def ambil_fundamental(kode):
    try:
        info = yf.Ticker(kode + ".JK").info or {}
    except Exception:
        info = {}
    def angka(k):
        v = info.get(k)
        return float(v) if isinstance(v, (int, float)) and np.isfinite(v) else None
    return {"nama": info.get("shortName") or kode,
            "PER": angka("trailingPE"), "PBV": angka("priceToBook"),
            "ROE": angka("returnOnEquity"), "DER": angka("debtToEquity"),
            "Dividen": angka("trailingAnnualDividendYield"), "Growth": angka("earningsGrowth")}

def cek_ihsg():
    try:
        c = yf.Ticker("^JKSE").history(period="2y")["Close"]
        if len(c) < 200:
            return None
        return bool(c.iloc[-1] > c.rolling(200).mean().iloc[-1])
    except Exception:
        return None

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

# ---------- INDIKATOR ----------
def rsi(c, n=14):
    d = c.diff()
    naik = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    turun = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + naik / turun.replace(0, np.nan))

def atr(df, n=14):
    c0 = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - c0).abs(), (df["Low"] - c0).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

def info_volume(df):
    v, c = df["Volume"], df["Close"]
    rata = v.rolling(20).mean().iloc[-1]
    rasio = float(v.iloc[-1] / rata) if rata > 0 else 0.0
    obv = (np.sign(c.diff()).fillna(0) * v).cumsum()
    akumulasi = bool(obv.iloc[-1] > obv.rolling(20).mean().iloc[-1])
    nilai_harian = float((c * v).rolling(20).mean().iloc[-1])
    return rasio, akumulasi, nilai_harian

# ---------- STRATEGI YANG DIUJI ----------
def posisi_dari_sinyal(masuk, keluar):
    s = pd.Series(np.nan, index=masuk.index)
    s[masuk] = 1.0
    s[keluar] = 0.0
    return s.ffill().fillna(0.0)

def kandidat(df):
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    out = []
    for f, s in [(5, 20), (10, 30), (10, 50), (20, 50), (20, 100), (50, 200)]:
        out.append((f"MA cross {f}/{s}", (c.rolling(f).mean() > c.rolling(s).mean()).astype(float)))
    r = rsi(c)
    for lo, hi in [(30, 70), (25, 65), (35, 65)]:
        out.append((f"RSI beli <{lo}, jual >{hi}", posisi_dari_sinyal(r < lo, r > hi)))
    ramai = v > VOLUME_RAMAI * v.rolling(20).mean()
    for n, m in [(20, 10), (55, 20)]:
        tembus = c > h.rolling(n).max().shift(1)
        jebol = c < l.rolling(m).min().shift(1)
        out.append((f"Breakout {n} hari", posisi_dari_sinyal(tembus, jebol)))
        out.append((f"Breakout {n} hari + volume", posisi_dari_sinyal(tembus & ramai, jebol)))
    return out

# ---------- BAGIAN "BELAJAR SENDIRI" ----------
def hasil_harian(c, pos):
    p = pos.shift(1).fillna(0)
    return p * c.pct_change().fillna(0) - p.diff().abs().fillna(0) * BIAYA

def sharpe(r):
    return 0.0 if r.std() == 0 else float(r.mean() / r.std() * np.sqrt(252))

def total(r):
    return float((1 + r).prod() - 1)

def belajar(df):
    c = df["Close"]
    batas = int(len(df) * 0.7)
    terbaik = None
    for nama, pos in kandidat(df):
        r = hasil_harian(c, pos)
        nilai = sharpe(r.iloc[:batas])
        if terbaik is None or nilai > terbaik["sharpe_latih"]:
            terbaik = {"nama": nama, "pos": pos, "sharpe_latih": nilai,
                       "ret_uji": total(r.iloc[batas:]), "sharpe_uji": sharpe(r.iloc[batas:])}
    terbaik["bh_uji"] = total(c.pct_change().fillna(0).iloc[batas:])
    terbaik["lulus"] = terbaik["ret_uji"] > 0 and terbaik["sharpe_uji"] > 0
    terbaik["mulai_uji"] = df.index[batas]
    return terbaik

# ---------- SKOR ----------
def skor_teknikal(df, m, akumulasi, ihsg_sehat):
    c = df["Close"]
    bobot = 1.0 if m["lulus"] else 0.4
    skor = 50 + (25 if m["pos"].iloc[-1] == 1 else -25) * bobot
    skor += 12 if c.iloc[-1] > c.rolling(200).mean().iloc[-1] else -12
    skor += 8 if c.iloc[-1] > c.iloc[-63] else -8
    skor += 5 if akumulasi else -5
    if ihsg_sehat is False:
        skor -= 15
    return float(np.clip(skor, 0, 100))

def tangga(x, anak_tangga, sisa):
    for batas, nilai in anak_tangga:
        if x < batas:
            return nilai
    return sisa

def skor_fundamental(f):
    s = []
    if f["PER"] is not None:
        s.append(0 if f["PER"] <= 0 else tangga(f["PER"], [(10, 100), (15, 75), (25, 50)], 25))
    if f["PBV"] is not None:
        s.append(tangga(f["PBV"], [(1, 100), (2, 75), (4, 50)], 25))
    if f["ROE"] is not None:
        s.append(tangga(-f["ROE"], [(-0.20, 100), (-0.15, 80), (-0.10, 60), (-0.05, 40)], 20))
    if f["DER"] is not None:
        s.append(tangga(f["DER"], [(50, 100), (100, 70), (200, 40)], 20))
    if f["Dividen"] is not None:
        s.append(tangga(-f["Dividen"], [(-0.05, 100), (-0.03, 75), (-0.01, 50)], 30))
    if f["Growth"] is not None:
        s.append(tangga(-f["Growth"], [(-0.15, 100), (-0.05, 70), (0, 50)], 20))
    return float(np.mean(s)) if len(s) >= 3 else None

def label(skor):
    if skor >= 70: return "🟢 Layak beli"
    if skor >= 55: return "🟡 Pantau / cicil"
    if skor >= 40: return "⚪ Tunggu"
    return "🔴 Hindari dulu"

# ---------- RENCANA TRANSAKSI ----------
def bulatkan(h):
    f = 1 if h < 200 else 2 if h < 500 else 5 if h < 2000 else 10 if h < 5000 else 25
    return int(max(h, 1) // f * f)

def hitung_lot(harga, stop):
    rugi_per_lot = (harga - stop) * 100
    if rugi_per_lot <= 0:
        return 0
    return int(max(0, min(MODAL * RISIKO / 100 // rugi_per_lot, MODAL // (harga * 100))))

# ---------- ANALISA SATU SAHAM ----------
def proses(kode, ihsg):
    df = ambil_harga(kode)
    if df is None:
        return None
    m, f = belajar(df), ambil_fundamental(kode)
    rasio, akumulasi, nilai_harian = info_volume(df)
    tek, fun = skor_teknikal(df, m, akumulasi, ihsg), skor_fundamental(f)
    akhir = tek if fun is None else 0.5 * tek + 0.5 * fun
    harga = float(df["Close"].iloc[-1])
    a = float(atr(df).iloc[-1])
    stop, target = bulatkan(harga - STOP_ATR * a), bulatkan(harga + TARGET_ATR * a)
    likuid = nilai_harian >= MIN_LIKUID * 1e9
    return {"kode": kode, "df": df, "m": m, "f": f, "tek": tek, "fun": fun, "akhir": akhir,
            "rekom": label(akhir) if likuid else "⚫ Kurang likuid",
            "posisi": int(m["pos"].iloc[-1]), "harga": harga, "stop": stop, "target": target,
            "lot": hitung_lot(harga, stop), "rasio": rasio, "akumulasi": akumulasi,
            "nilai_harian": nilai_harian, "likuid": likuid}

def cari_perubahan(hasil, pembanding):
    out = []
    for d in hasil:
        lama = pembanding.get(d["kode"])
        if not lama:
            continue
        if lama["posisi"] != d["posisi"]:
            out.append((d["kode"], "sinyal strategi berubah jadi " + ("BELI" if d["posisi"] == 1 else "JUAL")))
        if lama["rekom"] != d["rekom"]:
            out.append((d["kode"], f"{lama['rekom']} → {d['rekom']} (skor {lama['skor']} → {round(d['akhir'])})"))
    return out

# ---------- BIKIN HALAMAN ----------
def e(x):
    return html.escape(str(x))

def angka(x):
    return f"{x:,.0f}".replace(",", ".")

def rp(x):
    return "Rp " + angka(x)

def fmt(v, persen=False):
    if v is None: return "tidak ada data"
    return f"{v*100:.1f}%" if persen else f"{v:.2f}"

def bikin_grafik(d, pertama):
    df, m = d["df"], d["m"]
    c = df["Close"]
    lihat = c.iloc[-500:]
    fig = go.Figure()
    fig.add_scatter(x=lihat.index, y=lihat, name="Harga")
    fig.add_scatter(x=lihat.index, y=c.rolling(50).mean().reindex(lihat.index), name="MA50", line=dict(dash="dot"))
    fig.add_scatter(x=lihat.index, y=c.rolling(200).mean().reindex(lihat.index), name="MA200", line=dict(dash="dot"))
    ubah = m["pos"].diff().reindex(lihat.index)
    beli, jual = lihat[ubah == 1], lihat[ubah == -1]
    fig.add_scatter(x=beli.index, y=beli, mode="markers", name="Sinyal beli",
                    marker=dict(symbol="triangle-up", size=12, color="green"))
    fig.add_scatter(x=jual.index, y=jual, mode="markers", name="Sinyal jual",
                    marker=dict(symbol="triangle-down", size=12, color="red"))
    fig.add_vline(x=m["mulai_uji"], line_dash="dash", line_color="gray")
    fig.add_hline(y=d["stop"], line_dash="dot", line_color="red", annotation_text="Stop loss")
    fig.add_hline(y=d["target"], line_dash="dot", line_color="green", annotation_text="Target")
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=20, b=10), legend=dict(orientation="h"))
    return fig.to_html(full_html=False, include_plotlyjs="cdn" if pertama else False,
                       config={"responsive": True, "displayModeBar": False})

def blok_detail(d, pertama, ihsg):
    m, f = d["m"], d["f"]
    harga, stop, target, lot = d["harga"], d["stop"], d["target"], d["lot"]
    posisi = "PEGANG / BELI" if d["posisi"] == 1 else "DI LUAR / JUAL"
    ujian = "LULUS" if m["lulus"] else "GAGAL, sinyalnya kurang bisa dipercaya"
    teks_fun = "tidak cukup data" if d["fun"] is None else f"{d['fun']:.0f}/100"
    teks_ihsg = {True: "sehat", False: "lemah (skor dipotong 15)", None: "data tidak tersedia"}[ihsg]
    teks_vol = "akumulasi" if d["akumulasi"] else "distribusi"
    teks_likuid = "cukup likuid" if d["likuid"] else "⚠️ KURANG LIKUID, bisa susah dijual"
    if lot > 0:
        teks_lot = f"<b>{lot} lot</b> (sekitar {rp(lot * harga * 100)}), rugi sekitar {rp(lot * (harga - stop) * 100)} kalau kena stop loss"
    else:
        teks_lot = "<b>0 lot</b>, modal atau batas risikonya terlalu kecil buat saham ini"
    stop_pct = (stop / harga - 1) * 100
    target_pct = (target / harga - 1) * 100
    grafik = bikin_grafik(d, pertama)
    return f"""<details id="{d['kode']}">
<summary><b>{d['kode']}</b> {e(f['nama'])} · {d['rekom']} · skor {d['akhir']:.0f}</summary>
{grafik}
<p><b>Rencana transaksi</b> (kalau masuk di harga sekarang)</p>
<ul>
<li>Harga {rp(harga)} · Stop loss <b>{rp(stop)}</b> ({stop_pct:+.1f}%) · Target <b>{rp(target)}</b> ({target_pct:+.1f}%)</li>
<li>Maksimal beli: {teks_lot}</li>
</ul>
<p><b>Teknikal: {d['tek']:.0f}/100</b></p>
<ul>
<li>Strategi paling cocok: <b>{e(m['nama'])}</b>, posisi sekarang <b>{posisi}</b></li>
<li>Ujian sejak {m['mulai_uji']:%b %Y} (kanan garis abu-abu): strategi {m['ret_uji']*100:+.1f}% vs beli-diamkan {m['bh_uji']*100:+.1f}% → <b>{ujian}</b></li>
<li>Kondisi IHSG: {teks_ihsg}</li>
<li>Volume terakhir {d['rasio']:.1f}x rata-rata 20 hari, arah {teks_vol}</li>
<li>Nilai transaksi harian rata-rata {rp(d['nilai_harian'])} → {teks_likuid}</li>
</ul>
<p><b>Fundamental: {teks_fun}</b></p>
<ul>
<li>PER {fmt(f['PER'])} · PBV {fmt(f['PBV'])} · ROE {fmt(f['ROE'], True)}</li>
<li>DER {fmt(f['DER'])} · Dividen {fmt(f['Dividen'], True)} · Pertumbuhan laba {fmt(f['Growth'], True)}</li>
</ul>
</details>"""

CSS = """
body{font-family:system-ui,sans-serif;max-width:1000px;margin:0 auto;padding:16px;color:#1a1a1a;background:#fafafa;line-height:1.5}
h1{font-size:1.5rem;margin-bottom:0} h2{font-size:1.15rem;margin-top:32px;border-bottom:2px solid #ddd;padding-bottom:4px}
.info{color:#555;font-size:.9rem} .peringatan{background:#fff3cd;padding:8px 12px;border-radius:8px}
.kartu-wrap{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}
.kartu{background:#fff;border:1px solid #ddd;border-left:5px solid #2e9e4f;border-radius:8px;padding:12px}
.kartu .kode{font-size:1.2rem;font-weight:700} .kartu .skor{float:right;color:#2e9e4f;font-weight:700}
.kartu small{color:#555}
.geser{overflow-x:auto} table{border-collapse:collapse;width:100%;background:#fff;font-size:.9rem}
th,td{padding:6px 10px;border-bottom:1px solid #e5e5e5;text-align:right;white-space:nowrap}
th:first-child,td:first-child,th:nth-child(4),td:nth-child(4){text-align:left} th{background:#f0f0f0}
details{background:#fff;border:1px solid #ddd;border-radius:8px;padding:10px 14px;margin:8px 0}
summary{cursor:pointer} a{color:#0b5cad;text-decoration:none} footer{margin-top:32px;color:#777;font-size:.8rem}
"""

JS = """
function ukurUlang(){window.dispatchEvent(new Event('resize'));}
document.querySelectorAll('details').forEach(function(d){d.addEventListener('toggle',ukurUlang);});
function buka(){var el=document.getElementById(location.hash.slice(1));
  if(el&&el.tagName==='DETAILS'){el.open=true;el.scrollIntoView();}}
window.addEventListener('hashchange',buka);buka();
"""

def bikin_halaman(hasil, perubahan, tgl_pembanding, ihsg, gagal, tanggal_data):
    wib = datetime.now(timezone(timedelta(hours=7)))
    b = []
    b.append("<!doctype html><html lang='id'><head><meta charset='utf-8'>")
    b.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    b.append("<title>Ringkasan Saham Harian</title><style>" + CSS + "</style></head><body>")
    b.append("<h1>📈 Ringkasan Saham Harian</h1>")
    b.append(f"<p class='info'>Data bursa per {e(tanggal_data)} · diperbarui {wib:%d %b %Y %H:%M} WIB · {len(hasil)} saham</p>")
    if ihsg is True:
        b.append("<p>IHSG: <b>sehat</b> (di atas rata-rata 200 hari)</p>")
    elif ihsg is False:
        b.append("<p class='peringatan'>⚠️ IHSG <b>lemah</b> (di bawah rata-rata 200 hari). Semua skor teknikal dipotong 15.</p>")
    else:
        b.append("<p class='info'>Data IHSG gagal diambil, filter pasar dilewati.</p>")
    if gagal:
        b.append(f"<p class='info'>Gagal diambil: {e(', '.join(gagal))}</p>")

    # --- Top layak beli ---
    b.append("<h2>Top saham layak beli</h2>")
    top = [d for d in hasil if d["rekom"].startswith("🟢")][:JUMLAH_TOP]
    if top:
        b.append("<div class='kartu-wrap'>")
        for d in top:
            b.append(f"<div class='kartu'><span class='skor'>{d['akhir']:.0f}</span>"
                     f"<div class='kode'><a href='#{d['kode']}'>{d['kode']}</a></div>"
                     f"<small>{e(d['f']['nama'])}</small><br>"
                     f"Harga {rp(d['harga'])}<br>Stop loss {rp(d['stop'])}<br>Target {rp(d['target'])}<br>"
                     f"Maks <b>{d['lot']} lot</b></div>")
        b.append("</div>")
    else:
        b.append("<p>Hari ini gak ada saham yang skornya 70 ke atas. Gak beli apa-apa juga keputusan yang sah.</p>")

    # --- Perubahan ---
    b.append("<h2>Perubahan dari hari sebelumnya</h2>")
    if tgl_pembanding is None:
        b.append("<p class='info'>Belum ada data pembanding. Bagian ini mulai keisi di hari bursa berikutnya.</p>")
    elif perubahan:
        b.append(f"<p class='info'>Dibanding data {e(tgl_pembanding)}</p><ul>")
        for kode, teks in perubahan:
            b.append(f"<li><a href='#{kode}'><b>{kode}</b></a>: {e(teks)}</li>")
        b.append("</ul>")
    else:
        b.append(f"<p>Gak ada sinyal yang berubah dibanding data {e(tgl_pembanding)}.</p>")

    # --- Peringkat ---
    b.append("<h2>Peringkat lengkap</h2><div class='geser'><table><tr><th>Kode</th><th>Harga</th><th>Skor</th>"
             "<th>Rekomendasi</th><th>Sinyal</th><th>Stop loss</th><th>Target</th><th>Maks lot</th><th>Volume</th></tr>")
    for d in hasil:
        b.append(f"<tr><td><a href='#{d['kode']}'><b>{d['kode']}</b></a></td><td>{angka(d['harga'])}</td>"
                 f"<td>{d['akhir']:.0f}</td><td>{d['rekom']}</td><td>{'Beli' if d['posisi'] == 1 else 'Jual'}</td>"
                 f"<td>{angka(d['stop'])}</td><td>{angka(d['target'])}</td><td>{d['lot']}</td><td>{d['rasio']:.1f}x</td></tr>")
    b.append("</table></div>")

    # --- Detail ---
    b.append("<h2>Detail per saham</h2><p class='info'>Klik buat buka grafik dan penjelasannya.</p>")
    for i, d in enumerate(hasil):
        b.append(blok_detail(d, i == 0, ihsg))

    b.append("<footer>Bukan saran investasi. Performa masa lalu tidak menjamin hasil ke depan. "
             "Data dari Yahoo Finance, bisa telat atau tidak lengkap.</footer>")
    b.append("<script>" + JS + "</script></body></html>")
    return "\n".join(b)

# ---------- JALANKAN ----------
def utama():
    daftar = baca_daftar()
    ihsg = cek_ihsg()
    hasil, gagal = [], []
    for kode in daftar:
        print("Analisa", kode)
        d = proses(kode, ihsg)
        if d is None:
            gagal.append(kode)
        else:
            hasil.append(d)
        time.sleep(0.5)
    if not hasil:
        print("Semua saham gagal diambil. Halaman lama dibiarkan.")
        sys.exit(1)
    hasil.sort(key=lambda d: d["akhir"], reverse=True)
    tanggal_data = max(d["df"].index[-1] for d in hasil).strftime("%d %b %Y")

    os.makedirs("docs", exist_ok=True)
    lama = {}
    if os.path.exists("docs/data.json"):
        try:
            with open("docs/data.json") as fh:
                lama = json.load(fh)
        except Exception:
            lama = {}
    if lama.get("tanggal") and lama["tanggal"] != tanggal_data:
        pembanding, tgl_pembanding = lama.get("saham", {}), lama["tanggal"]
    else:
        pembanding, tgl_pembanding = lama.get("sebelumnya", {}), lama.get("tanggal_sebelumnya")
    perubahan = cari_perubahan(hasil, pembanding)

    with open("docs/index.html", "w", encoding="utf-8") as fh:
        fh.write(bikin_halaman(hasil, perubahan, tgl_pembanding, ihsg, gagal, tanggal_data))
    sekarang = {d["kode"]: {"rekom": d["rekom"], "posisi": d["posisi"], "skor": round(d["akhir"])} for d in hasil}
    with open("docs/data.json", "w", encoding="utf-8") as fh:
        json.dump({"tanggal": tanggal_data, "saham": sekarang,
                   "sebelumnya": pembanding, "tanggal_sebelumnya": tgl_pembanding}, fh, ensure_ascii=False)
    print("Selesai:", len(hasil), "saham,", len(gagal), "gagal")

utama()
