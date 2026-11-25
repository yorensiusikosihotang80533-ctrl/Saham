import html, os, sys, time
from datetime import datetime, timezone, timedelta
import numpy as np, pandas as pd
import yfinance as yf

# ================= PENGATURAN (boleh diubah) =================
BIAYA = 0.004            # total fee beli + jual (0,4%). Sesuaikan sama sekuritas lu
T_MIN = 2.0              # seberapa ketat syarat di data belajar (makin besar makin ketat)
MIN_HARI = 15            # periode dengan data kurang dari ini dilewati
MIN_UJI = 5              # minimal jumlah hari ujian biar pola boleh dinyatakan lolos
JUMLAH_TOP = 30          # berapa pola yang ditampilin di tabel utama
PERIODE_AWAL = "2 bulan" # periode yang kebuka pertama kali
# =============================================================

# (nama, jenis data, jumlah hari bursa terakhir yang dipakai)
PERIODE = [("1 bulan", "15m", 21), ("2 bulan", "15m", 42), ("3 bulan", "1h", 63),
           ("6 bulan", "1h", 126), ("1 tahun", "1h", 252), ("2 tahun", "1h", 9999)]
AMBIL = {"15m": "59d", "1h": "700d"}
NAMA_DATA = {"15m": "per 15 menit", "1h": "per jam"}

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

def ambil(kode, jenis):
    for _ in range(3):
        try:
            df = yf.Ticker(kode + ".JK").history(period=AMBIL[jenis], interval=jenis, auto_adjust=True)
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
    """Ubah data jadi tabel: satu baris per hari, satu kolom per slot waktu."""
    t = df.assign(hari=df.index.normalize(), slot=df.index.strftime("%H:%M"))
    buka = pd.pivot_table(t, index="hari", columns="slot", values="Open", aggfunc="first")
    tutup = pd.pivot_table(t, index="hari", columns="slot", values="Close", aggfunc="last")
    gerak = tutup / buka - 1
    # buang slot yang jarang ada, dan slot yang hampir selalu diam (catatan harga penutupan)
    pakai = [s for s in buka.columns
             if buka[s].notna().mean() >= 0.6 and (gerak[s].abs() > 0).mean() >= 0.05]
    if len(pakai) < 2:
        return None
    titik = buka[pakai].copy()
    titik["tutup"] = tutup.ffill(axis=1).iloc[:, -1]
    return titik, gerak[pakai]

def bikin_profil(gerak):
    out = []
    for s in gerak.columns:
        g = gerak[s].dropna()
        if len(g) > 0:
            out.append({"slot": s, "rata": float(g.mean()), "naik": float((g > 0).mean()),
                        "turun": float((g < 0).mean()), "n": len(g)})
    return out

def statistik(r):
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 2:
        return None
    sd = r.std(ddof=1)
    return {"n": n, "rata": float(r.mean()), "menang": float((r > 0).mean()),
            "t": float(r.mean() / (sd / np.sqrt(n))) if sd > 0 else 0.0}

def nilai(jenis, beli, jual, r, batas):
    latih, uji = statistik(r[:batas]), statistik(r[batas:])
    if latih is None or uji is None:
        return None
    lulus = (latih["rata"] > 0 and latih["t"] >= T_MIN and uji["rata"] > 0
             and uji["menang"] > 0.5 and uji["n"] >= MIN_UJI)
    return {"jenis": jenis, "beli": beli, "jual": jual, "latih": latih, "uji": uji,
            "lulus": lulus, "skor": min(latih["rata"], uji["rata"])}

def boleh_inap(beli, jual):
    """Menginap cuma dihitung kalau beli sore (14.00 ke atas) dan jual besok pagi (sebelum 11.00)."""
    return (beli == "tutup" or beli >= "14:00") and jual != "tutup" and jual < "11:00"

def cari(titik):
    """Coba semua kombinasi jam beli dan jam jual. Hasil sudah dipotong biaya."""
    nama = list(titik.columns)
    hari_ini = titik.to_numpy(dtype=float)
    besok = np.vstack([hari_ini[1:], np.full((1, hari_ini.shape[1]), np.nan)])
    batas = int(len(hari_ini) * 0.7)
    hasil = []
    with np.errstate(divide="ignore", invalid="ignore"):
        for i, a in enumerate(nama):
            for j, b in enumerate(nama):
                if j > i:
                    h = nilai("Hari yang sama", a, b, hari_ini[:, j] / hari_ini[:, i] - 1 - BIAYA, batas)
                    if h:
                        hasil.append(h)
                if boleh_inap(a, b):
                    h = nilai("Menginap", a, "besok " + b, besok[:, j] / hari_ini[:, i] - 1 - BIAYA, batas)
                    if h:
                        hasil.append(h)
    return hasil

# ---------- BIKIN HALAMAN ----------
def e(x):
    return html.escape(str(x))

def pct(x):
    return f"{x * 100:+.2f}%"

def ribuan(x):
    return f"{x:,}".replace(",", ".")

KEPALA_POLA = ("<tr><th>{}</th><th>Jenis</th><th>Beli</th><th>Jual</th><th>Rata-rata (belajar)</th>"
               "<th>Rata-rata (ujian)</th><th>Menang (ujian)</th><th>Hari ujian</th></tr>")

def baris_pola(kode, h, idx, utama_tabel):
    if utama_tabel:
        awal = f"<tr class='baris' data-kode='{kode}'><td><a href='#{kode}-{idx}'><b>{kode}</b></a></td>"
    else:
        awal = "<tr><td>" + ("✅" if h["lulus"] else "✗") + "</td>"
    return (awal + f"<td>{h['jenis']}</td><td>{e(h['beli'])}</td><td>{e(h['jual'])}</td>"
            f"<td>{pct(h['latih']['rata'])}</td><td>{pct(h['uji']['rata'])}</td>"
            f"<td>{h['uji']['menang'] * 100:.0f}%</td><td>{h['uji']['n']}</td></tr>")

def bikin_bagian(idx, nama, jenis, saham):
    b = [f"<section class='periode' id='p-{idx}' style='display:none'>"]
    if not saham:
        b.append("<p>Data buat periode ini gak tersedia hari ini.</p></section>")
        return "\n".join(b)
    awal = min(s["awal"] for s in saham)
    akhir = max(s["akhir"] for s in saham)
    total = sum(s["kombinasi"] for s in saham)
    lolos = sum(len(s["lulus"]) for s in saham)
    hari = max(s["hari"] for s in saham)
    b.append(f"<p class='info'>Data {NAMA_DATA[jenis]}, {awal:%d %b %Y} sampai {akhir:%d %b %Y} "
             f"(sekitar {hari} hari bursa) · {len(saham)} saham</p>")
    b.append(f"<p class='catatan'>Dari <b>{ribuan(total)}</b> kombinasi jam beli dan jam jual yang dicoba, "
             f"<b>{lolos}</b> lolos ujian. Semua angka pola sudah dipotong biaya {BIAYA * 100:.1f}% bolak-balik. "
             "Karena yang dicoba ribuan, sebagian yang lolos pasti cuma kebetulan.</p>")
    if hari <= 70:
        b.append("<p class='catatan'>⚠️ Periode pendek = sampel kecil. Pola di sini lebih cepat nangkep kebiasaan baru, "
                 "tapi juga jauh lebih gampang muncul karena kebetulan. Lihat kolom Hari ujian: makin sedikit, makin lemah buktinya.</p>")

    b.append("<h2>Pola paling konsisten</h2>")
    terbaik = sorted([(s["kode"], s["lulus"][0]) for s in saham if s["lulus"]],
                     key=lambda x: x[1]["skor"], reverse=True)[:JUMLAH_TOP]
    if terbaik:
        b.append("<p class='info'>Satu pola terbaik per saham. Jam beli dan jual = harga di awal slot itu, "
                 "\"tutup\" = harga penutupan.</p>")
        b.append("<div class='geser'><table>" + KEPALA_POLA.format("Kode"))
        for kode, h in terbaik:
            b.append(baris_pola(kode, h, idx, True))
        b.append("</table></div>")
    else:
        b.append("<p>Gak ada pola yang lolos ujian setelah dipotong biaya di periode ini.</p>")

    b.append("<h2>Detail per saham</h2><p class='info'>Klik buat lihat gerak rata-rata tiap slot waktu dan 5 pola teratasnya.</p>")
    for s in sorted(saham, key=lambda s: (len(s["lulus"]) == 0, s["kode"])):
        tanda = f"✅ {len(s['lulus'])} pola lolos" if s["lulus"] else "tidak ada pola yang lolos"
        b.append(f"<details class='blok' data-kode='{s['kode']}' id='{s['kode']}-{idx}'>"
                 f"<summary><b>{s['kode']}</b> · {tanda} · {s['hari']} hari data</summary>")
        b.append("<p><b>Gerak rata-rata tiap slot waktu</b> (belum dipotong biaya)</p>")
        b.append("<div class='geser'><table class='profil'><tr><th>Jam</th><th>Rata-rata gerak</th><th>Hari naik</th>"
                 "<th>Hari turun</th><th>Jumlah hari</th></tr>")
        for p in s["profil"]:
            b.append(f"<tr><td>{p['slot']}</td><td>{pct(p['rata'])}</td><td>{p['naik'] * 100:.0f}%</td>"
                     f"<td>{p['turun'] * 100:.0f}%</td><td>{p['n']}</td></tr>")
        b.append("</table></div>")
        b.append("<p><b>5 pola teratas</b> (sudah dipotong biaya)</p>")
        b.append("<div class='geser'><table>" + KEPALA_POLA.format("Lolos"))
        for h in s["pola"]:
            b.append(baris_pola(s["kode"], h, idx, False))
        b.append("</table></div></details>")
    b.append("</section>")
    return "\n".join(b)

CSS = """
body{font-family:system-ui,sans-serif;max-width:1000px;margin:0 auto;padding:16px;color:#1a1a1a;background:#fafafa;line-height:1.5}
h1{font-size:1.5rem;margin-bottom:0} h2{font-size:1.15rem;margin-top:32px;border-bottom:2px solid #ddd;padding-bottom:4px}
.info{color:#555;font-size:.9rem} .catatan{background:#fff3cd;padding:8px 12px;border-radius:8px;font-size:.9rem}
.tab{display:flex;flex-wrap:wrap;gap:6px;margin:16px 0 8px}
.tab button{font-size:.95rem;padding:8px 14px;border:1px solid #999;border-radius:20px;background:#fff;cursor:pointer}
.tab button.aktif{background:#0b5cad;color:#fff;border-color:#0b5cad;font-weight:700}
#cari{font-size:1rem;padding:6px 10px;border:1px solid #999;border-radius:6px;width:220px}
.geser{overflow-x:auto} table{border-collapse:collapse;width:100%;background:#fff;font-size:.9rem;margin:8px 0}
th,td{padding:6px 10px;border-bottom:1px solid #e5e5e5;text-align:right;white-space:nowrap}
th:nth-child(-n+4),td:nth-child(-n+4){text-align:left} th{background:#f0f0f0}
table.profil th,table.profil td{text-align:right} table.profil th:first-child,table.profil td:first-child{text-align:left}
details{background:#fff;border:1px solid #ddd;border-radius:8px;padding:10px 14px;margin:8px 0}
summary{cursor:pointer} a{color:#0b5cad;text-decoration:none} footer{margin-top:32px;color:#777;font-size:.8rem}
"""

JS = """
function pilih(p){
  document.querySelectorAll('section.periode').forEach(function(s){s.style.display=(s.id==='p-'+p)?'':'none';});
  document.querySelectorAll('.tab button').forEach(function(t){t.className=(t.dataset.p===String(p))?'aktif':'';});
  try{localStorage.setItem('periode',p);}catch(err){}
}
document.querySelectorAll('.tab button').forEach(function(t){t.addEventListener('click',function(){pilih(t.dataset.p);});});
var awal=PERIODE_AWAL_IDX;
try{var simpan=localStorage.getItem('periode'); if(simpan!==null&&document.getElementById('p-'+simpan)){awal=simpan;}}catch(err){}
pilih(awal);

function saring(){
  var cari=document.getElementById('cari').value.toUpperCase().split(/[ ,]+/).filter(function(k){return k;});
  document.querySelectorAll('details.blok, tr.baris').forEach(function(el){
    var ok=cari.length===0||cari.some(function(k){return el.dataset.kode.indexOf(k)>=0;});
    el.style.display=ok?'':'none';
  });
}
document.getElementById('cari').addEventListener('input',saring);

function buka(){var el=document.getElementById(location.hash.slice(1));
  if(el&&el.tagName==='DETAILS'){el.style.display='';el.open=true;el.scrollIntoView();}}
window.addEventListener('hashchange',buka);buka();
"""

def bikin_halaman(per, gagal):
    wib = datetime.now(timezone(timedelta(hours=7)))
    nama_semua = [p[0] for p in PERIODE]
    idx_awal = nama_semua.index(PERIODE_AWAL) if PERIODE_AWAL in nama_semua else 0
    b = []
    b.append("<!doctype html><html lang='id'><head><meta charset='utf-8'>")
    b.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    b.append("<title>Pola Jam-jaman Saham</title><style>" + CSS + "</style></head><body>")
    b.append("<h1>⏱ Pola Jam-jaman Saham</h1>")
    b.append("<p><a href='index.html'>← Kembali ke ringkasan harian</a></p>")
    b.append(f"<p class='info'>Diperbarui {wib:%d %b %Y %H:%M} WIB</p>")
    if gagal:
        b.append(f"<p class='info'>Dilewati (data gagal diambil): {e(', '.join(gagal))}</p>")
    b.append("<div class='tab'>")
    for i, (nama, jenis, _) in enumerate(PERIODE):
        b.append(f"<button type='button' data-p='{i}'>{nama}</button>")
    b.append("</div>")
    b.append("<input id='cari' type='text' placeholder='Cari kode, misal BREN, CUAN'>")
    for i, (nama, jenis, _) in enumerate(PERIODE):
        b.append(bikin_bagian(i, nama, jenis, per[i]))
    b.append("<footer>Bukan saran investasi. Pola masa lalu bisa hilang kapan saja. "
             "Data dari Yahoo Finance, bisa telat atau tidak lengkap.</footer>")
    b.append("<script>" + JS.replace("PERIODE_AWAL_IDX", str(idx_awal)) + "</script></body></html>")
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
    per = [[] for _ in PERIODE]
    gagal = []
    for kode in daftar:
        print("Pola", kode)
        data = {}
        for jenis in AMBIL:
            df = ambil(kode, jenis)
            data[jenis] = susun(df) if df is not None and len(df) > 0 else None
        if all(v is None for v in data.values()):
            gagal.append(kode)
            continue
        for i, (nama, jenis, n) in enumerate(PERIODE):
            if data[jenis] is None:
                continue
            titik, gerak = data[jenis]
            titik, gerak = titik.tail(n), gerak.tail(n)
            if len(titik) < MIN_HARI:
                continue
            pola = cari(titik)
            pola.sort(key=lambda h: h["skor"], reverse=True)
            per[i].append({"kode": kode, "hari": len(titik), "awal": titik.index[0], "akhir": titik.index[-1],
                           "profil": bikin_profil(gerak), "kombinasi": len(pola),
                           "lulus": [h for h in pola if h["lulus"]], "pola": pola[:5]})
        time.sleep(0.3)
    if not any(per):
        print("Semua saham gagal diambil. Halaman pola lama dibiarkan.")
        sys.exit(1)
    os.makedirs("docs", exist_ok=True)
    with open("docs/pola.html", "w", encoding="utf-8") as fh:
        fh.write(bikin_halaman(per, gagal))
    pasang_tautan()
    print("Selesai. Jumlah saham per periode:", [len(p) for p in per], "| dilewati:", len(gagal))

utama()
