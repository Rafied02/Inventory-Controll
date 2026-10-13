"""Perhitungan KPI & laporan inventory (murni pandas, tanpa Streamlit agar mudah diuji)."""
import io
from datetime import datetime

import numpy as np
import pandas as pd


def rp(x):
    return "Rp " + f"{x:,.0f}".replace(",", ".")


def stok_pada(p, t, waktu):
    """Stok setiap SKU (urutan sama dengan p) tepat sebelum `waktu`."""
    tt = t[t["Waktu"] < waktu]
    d = np.where(tt["Tipe"] == "Keluar", -tt["Qty"], tt["Qty"])
    net = pd.Series(d, index=tt.index).groupby(tt["SKU"]).sum()
    return p["Stok_Awal"] + p["SKU"].map(net).fillna(0)


def _keluar(p, t, mulai, akhir):
    x = t[(t["Waktu"] >= mulai) & (t["Waktu"] < akhir) & (t["Tipe"] == "Keluar")]
    x = x.merge(p[["SKU", "Harga_Beli", "Harga_Jual"]], on="SKU", how="left")
    return x, float((x["Qty"] * x["Harga_Jual"]).sum()), float((x["Qty"] * x["Harga_Beli"]).sum())


def hitung_laporan(p, t, mulai, akhir, lead=7, cover=14):
    """p = hasil hitung_stok() (punya Stok, Status, Nilai_Stok). mulai/akhir: pd.Timestamp, akhir eksklusif."""
    n = max((akhir - mulai).days, 1)
    x, rev, cogs = _keluar(p, t, mulai, akhir)
    _, rev0, cogs0 = _keluar(p, t, mulai - (akhir - mulai), mulai)

    # --- inventory turnover & DOI ---
    s0, s1 = stok_pada(p, t, mulai), stok_pada(p, t, akhir)
    avg_val = float(((s0 + s1) / 2 * p["Harga_Beli"]).sum())
    turnover = cogs / avg_val if avg_val > 0 else 0.0
    turnover_tahunan = turnover * 365 / n
    doi = n / turnover if turnover > 0 else np.nan

    in_per = t[(t["Waktu"] >= mulai) & (t["Waktu"] < akhir)]
    masuk = float(in_per.loc[in_per["Tipe"] == "Masuk", "Qty"].sum())
    keluar_qty = float(x["Qty"].sum())
    tersedia = float(s0.clip(lower=0).sum()) + masuk
    sell_through = keluar_qty / tersedia * 100 if tersedia > 0 else 0.0

    # --- tabel per SKU ---
    per = x.groupby("SKU")["Qty"].sum().rename("Qty_Keluar")
    base = p.merge(per, on="SKU", how="left")
    base["Qty_Keluar"] = base["Qty_Keluar"].fillna(0)
    base["Pendapatan"] = base["Qty_Keluar"] * base["Harga_Jual"]
    base["HPP"] = base["Qty_Keluar"] * base["Harga_Beli"]
    base["Laba"] = base["Pendapatan"] - base["HPP"]
    base["Rata2_Harian"] = base["Qty_Keluar"] / n
    base["Cakupan_Hari"] = np.where(base["Rata2_Harian"] > 0, base["Stok"] / base["Rata2_Harian"].replace(0, np.nan), np.nan)
    base["Saran_Order"] = np.ceil(np.maximum(0, base["Rata2_Harian"] * (lead + cover) - base["Stok"])).astype(int)
    base["Est_Biaya"] = base["Saran_Order"] * base["Harga_Beli"]

    abc = base[base["Pendapatan"] > 0].sort_values("Pendapatan", ascending=False).copy()
    if not abc.empty:
        abc["Kum_%"] = abc["Pendapatan"].cumsum() / abc["Pendapatan"].sum() * 100
        sebelum = abc["Kum_%"].shift(fill_value=0)
        abc["Kelas"] = np.where(sebelum < 80, "A", np.where(sebelum < 95, "B", "C"))
    else:
        abc["Kum_%"], abc["Kelas"] = [], []

    restock = base[base["Saran_Order"] > 0].sort_values("Cakupan_Hari")
    dead = base[(base["Stok"] > 0) & (base["Qty_Keluar"] == 0)].sort_values("Nilai_Stok", ascending=False)

    harian = x.assign(Tanggal=x["Waktu"].dt.date, Pendapatan=x["Qty"] * x["Harga_Jual"],
                      Laba=x["Qty"] * (x["Harga_Jual"] - x["Harga_Beli"]))
    harian = harian.groupby("Tanggal")[["Pendapatan", "Laba"]].sum().reset_index()

    kategori = base.groupby("Kategori")[["Pendapatan", "HPP", "Laba", "Nilai_Stok"]].sum().reset_index()
    kategori["Margin_%"] = np.where(kategori["Pendapatan"] > 0, kategori["Laba"] / kategori["Pendapatan"].replace(0, np.nan) * 100, 0)

    # --- KPI ---
    laba = rev - cogs
    nilai_total = float(p["Nilai_Stok"].sum())
    kpi = dict(
        n_hari=n, pendapatan=rev, pendapatan_prev=rev0, hpp=cogs, hpp_prev=cogs0,
        laba=laba, laba_prev=rev0 - cogs0,
        margin=laba / rev * 100 if rev else 0.0,
        margin_prev=(rev0 - cogs0) / rev0 * 100 if rev0 else 0.0,
        turnover=turnover, turnover_tahunan=turnover_tahunan, doi=doi, sell_through=sell_through,
        stockout=float((p["Status"] == "Habis").mean() * 100),
        kritis=float(p["Status"].isin(["Kritis", "Habis"]).mean() * 100),
        dead_nilai=float(dead["Nilai_Stok"].sum()), dead_sku=len(dead),
        dead_pct=float(dead["Nilai_Stok"].sum() / nilai_total * 100) if nilai_total else 0.0,
        adj_pct=float((in_per["Tipe"] == "Penyesuaian").mean() * 100) if len(in_per) else 0.0,
        nilai_stok=nilai_total, n_trx=len(in_per), unit_keluar=keluar_qty, unit_masuk=masuk,
        restock_sku=len(restock), restock_biaya=float(restock["Est_Biaya"].sum()),
    )

    def cek(ok):
        return "✅ Tercapai" if ok else "⚠️ Perlu perhatian"

    skor = pd.DataFrame([
        ("Margin Kotor", f"{kpi['margin']:.1f}%", "≥ 25%", cek(kpi["margin"] >= 25)),
        ("Inventory Turnover (setahun)", f"{turnover_tahunan:.1f}x", "≥ 6x", cek(turnover_tahunan >= 6)),
        ("Days of Inventory", "-" if np.isnan(doi) else f"{doi:.0f} hari", "≤ 60 hari", cek(not np.isnan(doi) and doi <= 60)),
        ("Sell-Through Rate", f"{sell_through:.1f}%", "≥ 50%", cek(sell_through >= 50)),
        ("Stockout Rate (SKU habis)", f"{kpi['stockout']:.1f}%", "≤ 5%", cek(kpi["stockout"] <= 5)),
        ("SKU Kritis/Habis", f"{kpi['kritis']:.1f}%", "≤ 15%", cek(kpi["kritis"] <= 15)),
        ("Dead Stock (nilai)", f"{kpi['dead_pct']:.1f}%", "≤ 10%", cek(kpi["dead_pct"] <= 10)),
        ("Tingkat Penyesuaian Stok", f"{kpi['adj_pct']:.1f}%", "≤ 5%", cek(kpi["adj_pct"] <= 5)),
    ], columns=["KPI", "Nilai", "Target", "Status"])

    return dict(kpi=kpi, skor=skor, base=base, abc=abc, restock=restock, dead=dead,
                harian=harian, kategori=kategori, mulai=mulai, akhir=akhir, lead=lead, cover=cover)


# ---------- ekspor ----------
KOL_RESTOCK = ["SKU", "Nama", "Supplier", "Stok", "Stok_Min", "Rata2_Harian", "Cakupan_Hari", "Saran_Order", "Est_Biaya"]
KOL_ABC = ["SKU", "Nama", "Kategori", "Qty_Keluar", "Pendapatan", "Laba", "Kum_%", "Kelas"]
KOL_DEAD = ["SKU", "Nama", "Kategori", "Stok", "Nilai_Stok"]


def buat_excel(h):
    k = h["kpi"]
    ringkas = pd.DataFrame({"Metrik": ["Periode", "Pendapatan", "HPP", "Laba Kotor", "Margin Kotor %", "Inventory Turnover",
                                       "Days of Inventory", "Sell-Through %", "Nilai Persediaan", "Dead Stock (nilai)"],
                            "Nilai": [f"{h['mulai']:%d %b %Y} - {(h['akhir'] - pd.Timedelta(days=1)):%d %b %Y}",
                                      k["pendapatan"], k["hpp"], k["laba"], round(k["margin"], 2), round(k["turnover"], 2),
                                      None if np.isnan(k["doi"]) else round(k["doi"], 1), round(k["sell_through"], 2),
                                      k["nilai_stok"], k["dead_nilai"]]})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        ringkas.to_excel(w, sheet_name="Ringkasan", index=False)
        h["skor"].to_excel(w, sheet_name="Scorecard KPI", index=False)
        h["abc"][KOL_ABC].to_excel(w, sheet_name="Analisis ABC", index=False)
        h["restock"][KOL_RESTOCK].to_excel(w, sheet_name="Saran Restock", index=False)
        h["dead"][KOL_DEAD].to_excel(w, sheet_name="Dead Stock", index=False)
        h["kategori"].to_excel(w, sheet_name="Per Kategori", index=False)
        for ws in w.book.worksheets:
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(
                    max(len(str(c.value)) if c.value is not None else 0 for c in col) + 3, 45)
    return buf.getvalue()


def buat_html(h, judul="Laporan Inventory"):
    k = h["kpi"]
    periode = f"{h['mulai']:%d %b %Y} - {(h['akhir'] - pd.Timedelta(days=1)):%d %b %Y}"

    def tabel(df, fmt=None):
        return df.to_html(index=False, border=0, classes="t", formatters=fmt or {}, float_format=lambda v: f"{v:,.1f}")

    rpf = {c: rp for c in ["Pendapatan", "Laba", "Est_Biaya", "Nilai_Stok", "HPP"]}
    kartu = [("Pendapatan", rp(k["pendapatan"])), ("Laba Kotor", rp(k["laba"])), ("Margin", f"{k['margin']:.1f}%"),
             ("Turnover", f"{k['turnover']:.2f}x"), ("DOI", "-" if np.isnan(k["doi"]) else f"{k['doi']:.0f} hari"),
             ("Nilai Persediaan", rp(k["nilai_stok"]))]
    kartu_html = "".join(f"<div class='k'><span>{a}</span><b>{b}</b></div>" for a, b in kartu)
    return f"""<!doctype html><html lang="id"><head><meta charset="utf-8"><title>{judul}</title><style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:32px;color:#0f172a}}h1{{margin:0}}h2{{margin-top:28px;border-bottom:2px solid #e2e8f0;padding-bottom:4px}}
.sub{{color:#64748b;margin-bottom:18px}}.g{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
.k{{border:1px solid #e2e8f0;border-radius:10px;padding:12px 16px;background:#f8fafc}}.k span{{display:block;color:#64748b;font-size:13px}}.k b{{font-size:20px}}
table.t{{border-collapse:collapse;width:100%;font-size:13px}}.t th{{background:#1e293b;color:#fff;text-align:left;padding:6px 8px}}.t td{{padding:6px 8px;border-bottom:1px solid #e2e8f0}}
@media print{{body{{margin:12px}}}}</style></head><body>
<h1>{judul}</h1><div class="sub">Periode {periode} · dibuat {datetime.now():%d %b %Y %H:%M}</div>
<div class="g">{kartu_html}</div>
<h2>Scorecard KPI</h2>{tabel(h['skor'])}
<h2>Analisis ABC (berdasarkan pendapatan)</h2>{tabel(h['abc'][KOL_ABC].head(15), rpf)}
<h2>Saran Restock (lead time {h['lead']} hari, cakupan {h['cover']} hari)</h2>{tabel(h['restock'][KOL_RESTOCK], rpf)}
<h2>Dead Stock (tidak ada penjualan di periode)</h2>{tabel(h['dead'][KOL_DEAD], rpf)}
<h2>Kinerja per Kategori</h2>{tabel(h['kategori'], rpf)}
</body></html>"""