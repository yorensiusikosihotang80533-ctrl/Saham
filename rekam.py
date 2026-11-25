import html, json, os, sys, time
from datetime import datetime, timezone, timedelta
import numpy as np, pandas as pd
import yfinance as yf

# ================= PENGATURAN (boleh diubah) =================
MAKS_HARI = 20     # posisi ditutup paksa setelah sekian hari bursa
BIAYA = 0.004      # total fee beli + jual (0,4%)
STOP_ATR = 2       # samain dengan analisa.py
TARGET_ATR = 3     # samain dengan analisa.py
MIN_BERARTI = 30   # jumlah catatan selesai per kelompok sebelum angkanya layak dipercaya
# =============================================================

KURANG_LIKUID = "⚫ Kurang likuid"
URUTAN_REKOM = ["🟢 Layak beli", "🟡 Pantau / cicil", "⚪ Tunggu", "🔴 Hindari dulu"]
RENTANG_SKOR = [(80, 101, "80 ke atas"), (70, 80, "70 sampai 79"), (60, 70, "60 sampai 69"),
                (50, 60, "50 sampai 59"), (40, 50, "40 sampai 49"), (0, 40, "Di bawah 40")]
NAMA_STATUS = {"target": "🎯 Kena target", "stop": "🛑 Kena stop loss", "waktu": "⏱ Waktu habis",
               "berjalan": "Berjalan", "menunggu": "Menunggu masuk"}

def ambil(simbol):
    for _ in range(3):
        try:
            df = yf.Ticker(simbol).history(period="1y", auto_adjust=False)
            if df is not None and len(df) > 30:
                df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
                return df[["Open", "High", "Low", "Close"]].dropna()
        except Exception:
            pass
        time.sleep(3)
    return None

def atr(df, n=14):
    c0 = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - c0).abs(), (df["Low"] - c0).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

def bulatkan(h):
    f = 1 if h < 200 else 2 if h < 500 else 5 if h < 2000 else 10 if h < 5000 else 25
    return int(max(h, 1) // f * f)

def banding_ihsg(ihsg, awal, akhir):
    if ihsg is None:
        return None
    a, b = ihsg["Close"].asof(awal), ihsg["Close"].asof(akhir)
    if pd.isna(a) or pd.isna(b) or a <= 0:
        return None
    return float(b / a - 1)

def perbarui(r, df, ihsg):
    """Cek apakah catatan ini sudah masuk, kena target, kena stop loss, atau habis waktu."""
    sinyal = pd.Timestamp(r["tgl_sinyal"])
    sesudah = df[df.index > sinyal]
    if len(sesudah) == 0:
        return
    masuk = float(sesudah["Open"].iloc[0])
    if masuk <= 0:
        return
    r["status"] = "berjalan"
    r["tgl_masuk"] = sesudah.index[0].strftime("%Y-%m-%d")
    r["harga_masuk"] = masuk
    for i, (tgl, b) in enumerate(sesudah.iterrows()):
        keluar, status = None, None
        if b["Low"] <= r["stop"]:                       # kalau target dan stop kena di hari yang sama, dianggap stop
            keluar, status = min(float(b["Open"]), float(r["stop"])), "stop"
        elif b["High"] >= r["target"]:
            keluar, status = max(float(b["Open"]), float(r["target"])), "target"
        elif i + 1 >= MAKS_HARI:
            keluar, status = float(b["Close"]), "waktu"
        if keluar is not None:
            r["status"] = status
            r["tgl_keluar"] = tgl.strftime("%Y-%m-%d")
            r["harga_keluar"] = keluar
            r["hari"] = i + 1
            r["hasil"] = keluar / masuk - 1 - BIAYA
            r["ihsg"] = banding_ihsg(ihsg, sinyal, tgl)
            r.pop("hasil_sementara", None)
            return
    r["hari"] = len(sesudah)
    r["hasil_sementara"] = float(sesudah["Close"].iloc[-1]) / masuk - 1

# ---------- BIKIN HALAMAN ----------
def e(x):
    return html.escape(str(x))

def pct(x):
    return "-" if x is None else f"{x * 100:+.2f}%"

def angka(x):
    return f"{x:,.0f}".replace(",", ".")

def tgl_pendek(s):
    return datetime.strptime(s, "%Y-%m-%d").strftime("%d %b")

def baris_ringkas(nama, rek):
    n = len(rek)
    if n == 0:
        return f"<tr><td>{e(nama)}</td><td>0</td>" + "<td>-</td>" * 6 + "</tr>"
    bagian = lambda st: f"{sum(1 for r in rek if r['status'] == st) / n * 100:.0f}%"
    rata = float(np.mean([r["hasil"] for r in rek]))
    ada = [r["ihsg"] for r in rek if r.get("ihsg") is not None]
    ihsg = float(np.mean(ada)) if ada else None
    selisih = None if ihsg is None else rata - ihsg
    jumlah = str(n) if n >= MIN_BERARTI else f"{n} ⚠️"
    return (f"<tr><td>{e(nama)}</td><td>{jumlah}</td><td>{bagian('target')}</td><td>{bagian('stop')}</td>"
            f"<td>{bagian('waktu')}</td><td><b>{pct(rata)}</b></td><td>{pct(ihsg)}</td><td><b>{pct(selisih)}</b></td></tr>")

KEPALA_RINGKAS = ("<tr><th>{}</th><th>Selesai</th><th>Kena target</th><th>Kena stop loss</th><th>Waktu habis</th>"
                  "<th>Rata-rata hasil</th><th>IHSG periode sama</th><th>Selisih</th></tr>")

CSS = """
body{font-family:system-ui,sans-serif;max-width:1000px;margin:0 auto;padding:16px;color:#1a1a1a;background:#fafafa;line-height:1.5}
h1{font-size:1.5rem;margin-bottom:0} h2{font-size:1.15rem;margin-top:32px;border-bottom:2px solid #ddd;padding-bottom:4px}
.info{color:#555;font-size:.9rem} .catatan{background:#fff3cd;padding:8px 12px;border-radius:8px;font-size:.9rem}
.geser{overflow-x:auto} table{border-collapse:collapse;width:100%;background:#fff;font-size:.9rem;margin:8px 0}
th,td{padding:6px 10px;border-bottom:1px solid #e5e5e5;text-align:right;white-space:nowrap}
th:first-child,td:first-child,th.kiri,td.kiri{text-align:left} th{background:#f0f0f0}
a{color:#0b5cad;text-decoration:none} footer{margin-top:32px;color:#777;font-size:.8rem}
"""

def bikin_halaman(rekam):
    wib = datetime.now(timezone(timedelta(hours=7)))
    selesai = [r for r in rekam if r["status"] in ("target", "stop", "waktu")]
    jalan = [r for r in rekam if r["status"] == "berjalan"]
    tunggu = [r for r in rekam if r["status"] == "menunggu"]
    b = []
    b.append("<!doctype html><html lang='id'><head><meta charset='utf-8'>")
    b.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    b.append("<title>Rekam Jejak Rekomendasi</title><style>" + CSS + "</style></head><body>")
    b.append("<h1>📒 Rekam Jejak Rekomendasi</h1>")
    b.append("<p><a href='index.html'>← Kembali ke ringkasan harian</a></p>")
    b.append(f"<p class='info'>Diperbarui {wib:%d %b %Y %H:%M} WIB · {len(selesai)} catatan selesai · "
             f"{len(jalan)} masih berjalan · {len(tunggu)} menunggu masuk</p>")
    b.append(f"<p class='catatan'>Aturan: masuk di harga pembukaan sehari setelah rekomendasi, keluar saat kena target, "
             f"kena stop loss, atau setelah {MAKS_HARI} hari bursa. Hasil sudah dipotong biaya {BIAYA * 100:.1f}%. "
             f"Tanda ⚠️ berarti catatannya belum sampai {MIN_BERARTI}, jadi angkanya belum bisa dijadiin pegangan.</p>")

    b.append("<h2>Hasil per rekomendasi</h2>")
    b.append("<p class='info'>Yang dicari: makin bagus rekomendasinya, makin tinggi kolom Rata-rata hasil dan Selisih. "
             "Kalau urutannya acak, skornya belum terbukti berguna.</p>")
    b.append("<div class='geser'><table>" + KEPALA_RINGKAS.format("Rekomendasi awal"))
    for nama in URUTAN_REKOM:
        b.append(baris_ringkas(nama, [r for r in selesai if r["rekom"] == nama]))
    b.append("</table></div>")

    b.append("<h2>Hasil per rentang skor</h2><div class='geser'><table>" + KEPALA_RINGKAS.format("Skor awal"))
    for bawah, atas, nama in RENTANG_SKOR:
        b.append(baris_ringkas(nama, [r for r in selesai if bawah <= r["skor"] < atas]))
    b.append("</table></div>")

    b.append("<h2>Masih berjalan</h2>")
    if jalan:
        b.append("<div class='geser'><table><tr><th>Kode</th><th class='kiri'>Rekomendasi awal</th><th>Skor</th><th>Masuk</th>"
                 "<th>Harga masuk</th><th>Stop loss</th><th>Target</th><th>Hari ke</th><th>Hasil sementara</th></tr>")
        for r in sorted(jalan, key=lambda r: r.get("hasil_sementara", 0), reverse=True):
            b.append(f"<tr><td><b>{r['kode']}</b></td><td class='kiri'>{e(r['rekom'])}</td><td>{r['skor']}</td>"
                     f"<td>{tgl_pendek(r['tgl_masuk'])}</td><td>{angka(r['harga_masuk'])}</td><td>{angka(r['stop'])}</td>"
                     f"<td>{angka(r['target'])}</td><td>{r.get('hari', 0)}</td><td>{pct(r.get('hasil_sementara'))}</td></tr>")
        b.append("</table></div>")
    else:
        b.append("<p class='info'>Belum ada. Catatan baru masuk di hari bursa setelah rekomendasinya keluar.</p>")

    b.append("<h2>50 catatan selesai terakhir</h2>")
    if selesai:
        b.append("<div class='geser'><table><tr><th>Kode</th><th class='kiri'>Rekomendasi awal</th><th>Skor</th><th>Masuk</th>"
                 "<th>Keluar</th><th class='kiri'>Akhirnya</th><th>Hari</th><th>Hasil</th><th>IHSG</th></tr>")
        for r in sorted(selesai, key=lambda r: r["tgl_keluar"], reverse=True)[:50]:
            b.append(f"<tr><td><b>{r['kode']}</b></td><td class='kiri'>{e(r['rekom'])}</td><td>{r['skor']}</td>"
                     f"<td>{tgl_pendek(r['tgl_masuk'])}</td><td>{tgl_pendek(r['tgl_keluar'])}</td>"
                     f"<td class='kiri'>{NAMA_STATUS[r['status']]}</td><td>{r['hari']}</td>"
                     f"<td><b>{pct(r['hasil'])}</b></td><td>{pct(r.get('ihsg'))}</td></tr>")
        b.append("</table></div>")
    else:
        b.append("<p class='info'>Belum ada catatan yang selesai. Yang paling cepat selesai biasanya butuh beberapa hari bursa.</p>")

    b.append("<footer>Ini simulasi di atas kertas, bukan transaksi beneran. Bukan saran investasi. "
             "Data dari Yahoo Finance, bisa telat atau tidak lengkap.</footer></body></html>")
    return "\n".join(b)

def pasang_tautan():
    jalur = "docs/index.html"
    if not os.path.exists(jalur):
        return
    with open(jalur, encoding="utf-8") as fh:
        isi = fh.read()
    if "rekam.html" in isi:
        return
    isi = isi.replace("</h1>", "</h1><p><a href='rekam.html'>📒 Lihat rekam jejak rekomendasi →</a></p>", 1)
    with open(jalur, "w", encoding="utf-8") as fh:
        fh.write(isi)

# ---------- JALANKAN ----------
def utama():
    if not os.path.exists("docs/data.json"):
        print("docs/data.json belum ada. Jalankan analisa.py dulu.")
        sys.exit(0)
    with open("docs/data.json", encoding="utf-8") as fh:
        data = json.load(fh)
    sinyal = pd.Timestamp(datetime.strptime(data["tanggal"], "%d %b %Y"))
    teks_sinyal = sinyal.strftime("%Y-%m-%d")

    rekam = []
    if os.path.exists("docs/rekam.json"):
        try:
            with open("docs/rekam.json", encoding="utf-8") as fh:
                rekam = json.load(fh)
        except Exception:
            rekam = []

    terbuka = {r["kode"] for r in rekam if r["status"] in ("menunggu", "berjalan")}
    semua_kode = sorted(set(data["saham"]) | terbuka)
    ihsg = ambil("^JKSE")
    harga = {}
    for kode in semua_kode:
        print("Rekam", kode)
        df = ambil(kode + ".JK")
        if df is not None:
            harga[kode] = df
        time.sleep(0.3)

    # catat rekomendasi baru
    baru = 0
    for kode, s in data["saham"].items():
        if kode in terbuka or s["rekom"] == KURANG_LIKUID or kode not in harga:
            continue
        if any(r["kode"] == kode and r["tgl_sinyal"] == teks_sinyal for r in rekam):
            continue
        df = harga[kode]
        if sinyal not in df.index:
            continue
        sampai = df.loc[:sinyal]
        if len(sampai) < 20:
            continue
        c = float(sampai["Close"].iloc[-1])
        a = float(atr(sampai).iloc[-1])
        rekam.append({"kode": kode, "tgl_sinyal": teks_sinyal, "rekom": s["rekom"], "skor": int(s["skor"]),
                        "harga_sinyal": c, "stop": int(s.get("stop") or bulatkan(c - STOP_ATR * a)),
                      "target": int(s.get("target") or bulatkan(c + TARGET_ATR * a)),
                      "target": int(s.get("target") or bulatkan(c + TARGET_ATR * a)),
                      "status": "menunggu"})
        baru += 1

    # perbarui catatan yang masih terbuka
    for r in rekam:
        if r["status"] in ("menunggu", "berjalan") and r["kode"] in harga:
            perbarui(r, harga[r["kode"]], ihsg)

    os.makedirs("docs", exist_ok=True)
    with open("docs/rekam.json", "w", encoding="utf-8") as fh:
        json.dump(rekam, fh, ensure_ascii=False)
    with open("docs/rekam.html", "w", encoding="utf-8") as fh:
        fh.write(bikin_halaman(rekam))
    pasang_tautan()
    print("Selesai:", baru, "catatan baru,", len(rekam), "total")

utama()
