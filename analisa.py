import html, json, os, sys, time
from datetime import datetime, timezone, timedelta
import numpy as np, pandas as pd
import yfinance as yf, plotly.graph_objects as go

# ================= PENGATURAN (boleh diubah) =================
MODAL = 10_000_000   # angka awal di kotak modal (bisa diganti langsung di halaman)
RISIKO = 1           # angka awal maksimal rugi per transaksi, % dari modal
MIN_LIKUID = 5       # minimal nilai transaksi harian, miliar rupiah
JUMLAH_TOP = 5       # berapa saham yang ditampilin di bagian "layak beli"
MACD_MIN_SKOR = 50   # MACD ditampilin buat saham yang skornya di atas angka ini
MACD_BARU = 3        # cross disebut "baru" kalau terjadi dalam sekian hari bursa terakhir
BIAYA = 0.002        # perkiraan fee + selisih harga tiap transaksi (0,2%)
STOP_ATR = 2         # stop loss = harga - 2x gerak harian rata-rata
TARGET_ATR = 3       # target    = harga + 3x gerak harian rata-rata
VOLUME_RAMAI = 1.5   # volume disebut ramai kalau 1,5x rata-rata 20 hari
# =============================================================

LABEL = ["🟢 Layak beli", "🟡 Pantau / cicil", "⚪ Tunggu", "🔴 Hindari dulu", "⚫ Kurang likuid"]
TEKS_MACD = {"baru-naik": "▲ Baru naik", "naik": "▲ Naik", "baru-turun": "▼ Baru turun", "turun": "▼ Turun"}

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
    return {"nama": info.get("shortName") or kode, "sektor": info.get("sector") or "Lainnya",
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

def info_macd(c):
    """MACD standar 12/26/9."""
    garis = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    sinyal = garis.ewm(span=9, adjust=False).mean()
    hist = garis - sinyal
    di_atas = hist > 0
    ganti = np.flatnonzero((di_atas != di_atas.shift(1)).to_numpy())
    hari = len(hist) - 1 - int(ganti[-1]) if len(ganti) else 999
    naik = bool(di_atas.iloc[-1])
    status = ("baru-" if hari <= MACD_BARU else "") + ("naik" if naik else "turun")
    return {"garis": garis, "sinyal": sinyal, "hist": hist, "status": status, "hari": hari,
            "menguat": bool(hist.iloc[-1] > hist.iloc[-2]), "atas_nol": bool(garis.iloc[-1] > 0)}

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
    if skor >= 70: return LABEL[0]
    if skor >= 55: return LABEL[1]
    if skor >= 40: return LABEL[2]
    return LABEL[3]

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
            "rekom": label(akhir) if likuid else LABEL[4],
            "posisi": int(m["pos"].iloc[-1]), "harga": harga, "stop": stop, "target": target,
            "lot": hitung_lot(harga, stop), "rasio": rasio, "akumulasi": akumulasi,
            "nilai_harian": nilai_harian, "likuid": likuid,
            "macd": info_macd(df["Close"]), "pakai_macd": round(akhir) > MACD_MIN_SKOR}

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

def data_lot(d):
    """Atribut yang dibaca halaman buat ngitung ulang lot saat modal diganti."""
    return f"data-harga='{d['harga']:.2f}' data-stop='{d['stop']}'"

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

def bikin_grafik_macd(d):
    mc = d["macd"]
    hist = mc["hist"].iloc[-500:]
    warna = ["#2e9e4f" if v >= 0 else "#d64545" for v in hist]
    fig = go.Figure()
    fig.add_bar(x=hist.index, y=hist, name="Histogram", marker_color=warna)
    fig.add_scatter(x=hist.index, y=mc["garis"].reindex(hist.index), name="MACD", line=dict(color="#0b5cad"))
    fig.add_scatter(x=hist.index, y=mc["sinyal"].reindex(hist.index), name="Sinyal", line=dict(color="#e08a00"))
    fig.update_layout(height=240, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h"))
    return fig.to_html(full_html=False, include_plotlyjs=False,
                       config={"responsive": True, "displayModeBar": False})

def teks_macd_panjang(mc):
    arah = TEKS_MACD[mc["status"]]
    sejak = "hari ini" if mc["hari"] == 0 else f"{mc['hari']} hari bursa lalu"
    tenaga = "membesar (dorongan menguat)" if mc["menguat"] else "mengecil (dorongan melemah)"
    nol = "di atas garis nol (tren menengah naik)" if mc["atas_nol"] else "di bawah garis nol (tren menengah masih turun)"
    return f"MACD: <b>{arah}</b>, cross terakhir {sejak}. Histogram {tenaga}. Garis MACD {nol}."

def blok_detail(d, pertama, ihsg):
    m, f = d["m"], d["f"]
    harga, stop, target = d["harga"], d["stop"], d["target"]
    posisi = "PEGANG / BELI" if d["posisi"] == 1 else "DI LUAR / JUAL"
    ujian = "LULUS" if m["lulus"] else "GAGAL, sinyalnya kurang bisa dipercaya"
    teks_fun = "tidak cukup data" if d["fun"] is None else f"{d['fun']:.0f}/100"
    teks_ihsg = {True: "sehat", False: "lemah (skor dipotong 15)", None: "data tidak tersedia"}[ihsg]
    teks_vol = "akumulasi" if d["akumulasi"] else "distribusi"
    teks_likuid = "cukup likuid" if d["likuid"] else "⚠️ KURANG LIKUID, bisa susah dijual"
    stop_pct = (stop / harga - 1) * 100
    target_pct = (target / harga - 1) * 100
    atribut = data_lot(d)
    grafik = bikin_grafik(d, pertama)
    grafik_macd = bikin_grafik_macd(d) if d["pakai_macd"] else ""
    baris_macd = "<li>" + teks_macd_panjang(d["macd"]) + "</li>" if d["pakai_macd"] else ""
    return f"""<details class="blok" id="{d['kode']}">
<summary><b>{d['kode']}</b> {e(f['nama'])} · {d['rekom']} · skor {d['akhir']:.0f}</summary>
{grafik}
{grafik_macd}
<p class="info">Sektor: {e(f['sektor'])}</p>
<p><b>Rencana transaksi</b> (kalau masuk di harga sekarang)</p>
<ul>
<li>Harga {rp(harga)} · Stop loss <b>{rp(stop)}</b> ({stop_pct:+.1f}%) · Target <b>{rp(target)}</b> ({target_pct:+.1f}%)</li>
<li>Maksimal beli: <span data-mode='panjang' {atribut}>{d['lot']} lot</span></li>
</ul>
<p><b>Teknikal: {d['tek']:.0f}/100</b></p>
<ul>
<li>Strategi paling cocok: <b>{e(m['nama'])}</b>, posisi sekarang <b>{posisi}</b></li>
<li>Ujian sejak {m['mulai_uji']:%b %Y} (kanan garis abu-abu): strategi {m['ret_uji']*100:+.1f}% vs beli-diamkan {m['bh_uji']*100:+.1f}% → <b>{ujian}</b></li>
{baris_macd}
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
.modal-box{background:#eef5ff;border:1px solid #b9d3f5;border-radius:8px;padding:12px;margin:16px 0}
.modal-box label{display:inline-block;margin:4px 16px 4px 0;font-weight:600}
.modal-box input{font-size:1rem;padding:6px 8px;border:1px solid #999;border-radius:6px;width:150px}
.modal-box input#risiko{width:60px} #modal-teks{font-weight:700;color:#0b5cad}
.filter{background:#f3f3f3;border:1px solid #ddd;border-radius:8px;padding:10px;margin:10px 0;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.filter input[type=text],.filter select,.filter button{font-size:.95rem;padding:6px 8px;border:1px solid #999;border-radius:6px;background:#fff}
.filter #f-cari{width:190px} .filter #f-skor{width:50px} .filter button{cursor:pointer}
.filter label{font-size:.9rem} #f-jumlah{width:100%;font-size:.9rem;color:#555}
.kartu-wrap{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}
.kartu{background:#fff;border:1px solid #ddd;border-left:5px solid #2e9e4f;border-radius:8px;padding:12px}
.kartu .kode{font-size:1.2rem;font-weight:700} .kartu .skor{float:right;color:#2e9e4f;font-weight:700}
.kartu small{color:#555}
.geser{overflow-x:auto} table{border-collapse:collapse;width:100%;background:#fff;font-size:.9rem}
th,td{padding:6px 10px;border-bottom:1px solid #e5e5e5;text-align:right;white-space:nowrap}
th:first-child,td:first-child,th:nth-child(4),td:nth-child(4),th:nth-child(10),td:nth-child(10),th:last-child,td:last-child{text-align:left}
th{background:#f0f0f0;cursor:pointer;user-select:none}
details{background:#fff;border:1px solid #ddd;border-radius:8px;padding:10px 14px;margin:8px 0}
summary{cursor:pointer} a{color:#0b5cad;text-decoration:none} footer{margin-top:32px;color:#777;font-size:.8rem}
"""

JS = """
function ukurUlang(){window.dispatchEvent(new Event('resize'));}
document.querySelectorAll('details').forEach(function(d){d.addEventListener('toggle',ukurUlang);});
function buka(){var el=document.getElementById(location.hash.slice(1));
  if(el&&el.tagName==='DETAILS'){el.style.display='';el.open=true;el.scrollIntoView();}}
window.addEventListener('hashchange',buka);buka();

// ----- filter -----
function ambilNilai(id){return document.getElementById(id).value;}
function saring(){
  var cari=ambilNilai('f-cari').toUpperCase().split(/[ ,]+/).filter(function(k){return k;});
  var rekom=ambilNilai('f-rekom'), sinyal=ambilNilai('f-sinyal'), sektor=ambilNilai('f-sektor'), macd=ambilNilai('f-macd');
  var skor=parseFloat(ambilNilai('f-skor'))||0;
  var lulus=document.getElementById('f-lulus').checked, kebeli=document.getElementById('f-lot').checked;
  var tampil={}, n=0, semua=0;
  document.querySelectorAll('tr.baris').forEach(function(tr){
    semua++;
    var d=tr.dataset, lot=parseInt(tr.querySelector('[data-harga]').textContent)||0;
    var okMacd=!macd||(macd.indexOf('baru')===0?d.macd===macd:d.macd.indexOf(macd)>=0);
    var ok=(cari.length===0||cari.some(function(k){return d.kode.indexOf(k)>=0;}))
      &&(!rekom||d.rekom===rekom)&&(!sinyal||d.sinyal===sinyal)&&(!sektor||d.sektor===sektor)&&okMacd
      &&parseFloat(d.skor)>=skor&&(!lulus||d.lulus==='1')&&(!kebeli||lot>=1);
    tr.style.display=ok?'':'none'; tampil[d.kode]=ok; if(ok){n++;}
  });
  document.querySelectorAll('details.blok').forEach(function(el){el.style.display=tampil[el.id]===false?'none':'';});
  document.getElementById('f-jumlah').textContent='Menampilkan '+n+' dari '+semua+' saham';
}
['f-cari','f-skor'].forEach(function(id){document.getElementById(id).addEventListener('input',saring);});
['f-rekom','f-sinyal','f-sektor','f-macd','f-lulus','f-lot'].forEach(function(id){document.getElementById(id).addEventListener('change',saring);});
document.getElementById('f-reset').addEventListener('click',function(){
  ['f-cari','f-rekom','f-sinyal','f-sektor','f-macd'].forEach(function(id){document.getElementById(id).value='';});
  document.getElementById('f-skor').value='0';
  document.getElementById('f-lulus').checked=false; document.getElementById('f-lot').checked=false;
  saring();
});

// ----- urutkan dengan klik judul kolom -----
document.querySelectorAll('#tabel-peringkat th').forEach(function(th,i){
  th.addEventListener('click',function(){
    var rows=Array.prototype.slice.call(document.querySelectorAll('#tabel-peringkat tr.baris'));
    var naik=th.dataset.arah!=='naik'; th.dataset.arah=naik?'naik':'turun';
    function isi(tr){var t=tr.children[i].textContent.trim();
      var x=parseFloat(t.split('.').join('').replace('x',''));return isNaN(x)?t:x;}
    rows.sort(function(a,b){var x=isi(a),y=isi(b);
      if(typeof x==='number'&&typeof y==='number'){return naik?x-y:y-x;}
      return naik?String(x).localeCompare(String(y)):String(y).localeCompare(String(x));});
    rows.forEach(function(r){r.parentNode.appendChild(r);});
  });
});

// ----- modal dan maks lot -----
var kotakModal=document.getElementById('modal'), kotakRisiko=document.getElementById('risiko');
function titik(x){return Math.round(x).toLocaleString('id-ID');}
function hitung(){
  var modal=parseFloat(kotakModal.value.replace(/[^0-9]/g,''))||0;
  var risiko=parseFloat(kotakRisiko.value.replace(',','.'))||0;
  document.getElementById('modal-teks').textContent='= Rp '+titik(modal)+', maks rugi Rp '+titik(modal*risiko/100)+' per transaksi';
  try{localStorage.setItem('modal',modal);localStorage.setItem('risiko',risiko);}catch(err){}
  document.querySelectorAll('[data-harga]').forEach(function(el){
    var h=parseFloat(el.dataset.harga), s=parseFloat(el.dataset.stop), rugi=(h-s)*100, lot=0;
    if(rugi>0){lot=Math.max(0,Math.min(Math.floor(modal*risiko/100/rugi),Math.floor(modal/(h*100))));}
    if(el.dataset.mode==='panjang'){
      el.innerHTML=lot>0
        ? '<b>'+lot+' lot</b> (sekitar Rp '+titik(lot*h*100)+'), rugi sekitar Rp '+titik(lot*rugi)+' kalau kena stop loss'
        : '<b>0 lot</b>, modal atau batas risikonya terlalu kecil buat saham ini';
    } else { el.textContent=lot; }
  });
  saring();
}
try{
  var m0=localStorage.getItem('modal'), r0=localStorage.getItem('risiko');
  if(m0&&parseFloat(m0)>0){kotakModal.value=m0;}
  if(r0&&parseFloat(r0)>0){kotakRisiko.value=r0;}
}catch(err){}
kotakModal.addEventListener('input',hitung);
kotakRisiko.addEventListener('input',hitung);
hitung();
"""

def bikin_filter(hasil):
    pilihan_rekom = "".join(f"<option value='{e(x)}'>{e(x)}</option>" for x in LABEL)
    sektor = sorted({d["f"]["sektor"] for d in hasil})
    pilihan_sektor = "".join(f"<option value='{e(x)}'>{e(x)}</option>" for x in sektor)
    return ("<div class='filter'>"
            "<input id='f-cari' type='text' placeholder='Cari kode, misal BREN, CUAN'>"
            f"<select id='f-rekom'><option value=''>Semua rekomendasi</option>{pilihan_rekom}</select>"
            "<select id='f-sinyal'><option value=''>Semua sinyal</option><option value='Beli'>Beli</option>"
            "<option value='Jual'>Jual</option></select>"
            "<select id='f-macd'><option value=''>Semua MACD</option><option value='naik'>MACD naik</option>"
            "<option value='baru-naik'>MACD baru naik</option><option value='turun'>MACD turun</option>"
            "<option value='baru-turun'>MACD baru turun</option></select>"
            f"<select id='f-sektor'><option value=''>Semua sektor</option>{pilihan_sektor}</select>"
            "<label>Skor minimal <input id='f-skor' type='text' inputmode='numeric' value='0'></label>"
            "<label><input type='checkbox' id='f-lulus'> Cuma yang lulus ujian</label>"
            "<label><input type='checkbox' id='f-lot'> Cuma yang kebeli</label>"
            "<button id='f-reset' type='button'>Reset</button>"
            "<div id='f-jumlah'></div></div>")

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

    # --- Kotak modal ---
    b.append("<div class='modal-box'>"
             f"<label>Modal hari ini (Rp) <input id='modal' type='text' inputmode='numeric' value='{MODAL}'></label>"
             f"<label>Maks rugi per transaksi (%) <input id='risiko' type='text' inputmode='decimal' value='{RISIKO}'></label>"
             "<div id='modal-teks'></div>"
             "<div class='info'>Maks lot di seluruh halaman langsung ngikutin angka ini. Angkanya cuma kesimpen di browser lu.</div>"
             "</div>")

    # --- Top layak beli ---
    b.append("<h2>Top saham layak beli</h2>")
    top = [d for d in hasil if d["rekom"] == LABEL[0]][:JUMLAH_TOP]
    if top:
        b.append("<div class='kartu-wrap'>")
        for d in top:
            macd_kartu = "<br>MACD " + TEKS_MACD[d["macd"]["status"]] if d["pakai_macd"] else ""
            b.append(f"<div class='kartu'><span class='skor'>{d['akhir']:.0f}</span>"
                     f"<div class='kode'><a href='#{d['kode']}'>{d['kode']}</a></div>"
                     f"<small>{e(d['f']['nama'])}</small><br>"
                     f"Harga {rp(d['harga'])}<br>Stop loss {rp(d['stop'])}<br>Target {rp(d['target'])}<br>"
                     f"Maks <b><span {data_lot(d)}>{d['lot']}</span> lot</b>{macd_kartu}</div>")
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

    # --- Peringkat + filter ---
    b.append("<h2>Peringkat lengkap</h2>")
    b.append(bikin_filter(hasil))
    b.append(f"<p class='info'>Klik judul kolom buat ngurutin. Kolom MACD cuma keisi buat saham dengan skor di atas {MACD_MIN_SKOR}.</p>")
    b.append("<div class='geser'><table id='tabel-peringkat'><tr><th>Kode</th><th>Harga</th><th>Skor</th>"
             "<th>Rekomendasi</th><th>Sinyal</th><th>Stop loss</th><th>Target</th><th>Maks lot</th>"
             "<th>Volume</th><th>MACD</th><th>Sektor</th></tr>")
    for d in hasil:
        sinyal = "Beli" if d["posisi"] == 1 else "Jual"
        lulus = "1" if d["m"]["lulus"] else "0"
        sektor = e(d["f"]["sektor"])
        kode_macd = d["macd"]["status"] if d["pakai_macd"] else ""
        teks_macd = TEKS_MACD[kode_macd] if kode_macd else "-"
        b.append(f"<tr class='baris' data-kode='{d['kode']}' data-skor='{d['akhir']:.0f}' data-rekom='{e(d['rekom'])}' "
                 f"data-sinyal='{sinyal}' data-lulus='{lulus}' data-sektor='{sektor}' data-macd='{kode_macd}'>"
                 f"<td><a href='#{d['kode']}'><b>{d['kode']}</b></a></td><td>{angka(d['harga'])}</td>"
                 f"<td>{d['akhir']:.0f}</td><td>{d['rekom']}</td><td>{sinyal}</td>"
                 f"<td>{angka(d['stop'])}</td><td>{angka(d['target'])}</td>"
                 f"<td {data_lot(d)}>{d['lot']}</td><td>{d['rasio']:.1f}x</td>"
                 f"<td>{teks_macd}</td><td>{sektor}</td></tr>")
    b.append("</table></div>")

    # --- Detail ---
    b.append("<h2>Detail per saham</h2><p class='info'>Klik buat buka grafik dan penjelasannya. Bagian ini ikut filter di atas.</p>")
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
