import streamlit as st
import json
import fitz
import pandas as pd
import re
from io import BytesIO

st.set_page_config(page_title="Comparador PDF vs JSON", layout="wide")

st.markdown("""
<style>
.semaforo-verde    { background:#1a472a; color:#a9f5c0; padding:18px 24px; border-radius:12px; font-size:22px; font-weight:bold; text-align:center; margin-bottom:10px; }
.semaforo-amarillo { background:#7a6000; color:#ffe680; padding:18px 24px; border-radius:12px; font-size:22px; font-weight:bold; text-align:center; margin-bottom:10px; }
.semaforo-rojo     { background:#6b1a1a; color:#ffaaaa; padding:18px 24px; border-radius:12px; font-size:22px; font-weight:bold; text-align:center; margin-bottom:10px; }
.sin-pareja        { background:#3a2000; color:#ffcc80; padding:10px 16px; border-radius:8px; margin-bottom:6px; font-size:14px; }
</style>
""", unsafe_allow_html=True)


# =========================================================
# FUNCIONES AUXILIARES
# =========================================================

def limpiar_texto(txt):
    if txt is None:
        return ""
    return str(txt).strip()

def limpiar_upper(txt):
    return limpiar_texto(txt).upper()

def convertir_a_float(valor):
    try:
        valor = str(valor).replace(",", ".").strip()
        return round(float(valor), 2)
    except:
        return None

def a_euro(valor):
    num = convertir_a_float(valor)
    if num is None:
        return ""
    return f"{num:.2f} €"

def crear_excel_en_memoria(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Diferencias")
    output.seek(0)
    return output.getvalue()

def son_textos_distintos(a, b):
    return limpiar_upper(a) != limpiar_upper(b)

def son_numeros_distintos(a, b):
    fa = convertir_a_float(a)
    fb = convertir_a_float(b)
    if fa is None and fb is None:
        return False
    if fa is None or fb is None:
        return True
    return fa != fb


# =========================================================
# PARSEAR JSON
# =========================================================

def parsear_json(data):
    resumen = {
        "pedido":   limpiar_texto(data.get("orderCode", "")),
        "cliente":  limpiar_texto(data.get("customerName", "")),
        "tienda":   limpiar_texto(data.get("storeName", "")),
        "proyecto": limpiar_texto(data.get("projectName", "")),
        "importe":  convertir_a_float(data.get("importe", 0)),
        "iva":      convertir_a_float(data.get("iva", 0)),
        "total":    convertir_a_float(data.get("total", 0)),
    }
    lineas = []
    for item in data.get("cabinets", []):
        lineas.append({
            "reference":   limpiar_texto(item.get("reference", "")),
            "name":        limpiar_texto(item.get("name", "")),
            "quantity":    convertir_a_float(item.get("quantity", "")),
            "total_linea": convertir_a_float(item.get("total", "")),
            "observation": limpiar_texto(item.get("observation", "")),
        })
    return resumen, lineas


# =========================================================
# PARSEAR PDF
# =========================================================

def extraer_texto_pdf(pdf_bytes):
    pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    texto = ""
    for page in pdf_doc:
        texto += page.get_text() + "\n"
    return texto

def limpiar_lineas(texto):
    return [line.strip() for line in texto.splitlines() if line.strip()]

def extraer_pedido_pdf(texto):
    matches = re.findall(r'\b20\d{12}\b', texto)
    if matches:
        return matches[0]
    return ""

def parsear_cabecera_pdf(texto):
    lineas  = limpiar_lineas(texto)
    pedido  = extraer_pedido_pdf(texto)
    cliente = ""
    tienda  = ""
    try:
        idx    = lineas.index("Cliente:")
        bloque = lineas[idx + 1: idx + 6]
        if len(bloque) >= 5:
            cliente = limpiar_texto(bloque[3])
            tienda  = limpiar_texto(bloque[4])
    except ValueError:
        pass
    return {"pedido": pedido, "cliente": cliente, "tienda": tienda}

def extraer_importes_pdf(texto):
    lineas  = limpiar_lineas(texto)
    numeros = [l for l in lineas if re.fullmatch(r"\d+\.\d{2}", l)]
    if len(numeros) >= 3:
        return {
            "importe": convertir_a_float(numeros[-3]),
            "iva":     convertir_a_float(numeros[-2]),
            "total":   convertir_a_float(numeros[-1]),
        }
    return {"importe": None, "iva": None, "total": None}

def es_referencia_valida(linea):
    linea = linea.strip()
    exclusiones = {
        "POS", "MUEBLE", "UD.", "DESCRIPCION", "IMPORTE",
        "MUEBLES", "BAJOS", "MURALES", "ALTOS",
        "REGLETAS", "COSTADOS", "DECORATIVOS",
        "COMPLEMENTOS", "ACCESORIOS"
    }
    if linea.upper() in exclusiones:
        return False
    if re.fullmatch(r"[A-Z0-9][A-Z0-9\.\-]*", linea):
        if re.fullmatch(r"\d", linea):
            return False
        return True
    return False

def parsear_lineas_pdf(texto):
    lineas     = limpiar_lineas(texto)
    resultados = []
    i = 0
    while i < len(lineas):
        actual = lineas[i]
        if es_referencia_valida(actual):
            referencia  = actual
            descripcion = ""
            cantidad    = None
            importe     = None
            for back in range(1, 4):
                if i - back >= 0 and re.fullmatch(r"\d+", lineas[i - back]):
                    cantidad = convertir_a_float(lineas[i - back])
                    break
            if i + 1 < len(lineas):
                descripcion = limpiar_texto(lineas[i + 1])
            for j in range(i + 1, min(i + 12, len(lineas))):
                if re.fullmatch(r"\d+\.\d{2}", lineas[j]):
                    importe = convertir_a_float(lineas[j])
                    break
            resultados.append({
                "reference":     limpiar_texto(referencia),
                "description":   descripcion,
                "quantity":      cantidad,
                "importe_linea": importe,
            })
        i += 1
    vistos = set()
    unicos = []
    for item in resultados:
        clave = (item["reference"], item["description"], item["importe_linea"])
        if clave not in vistos:
            vistos.add(clave)
            unicos.append(item)
    return unicos

def indexar_por_referencia(lineas):
    refs = {}
    for item in lineas:
        ref = limpiar_upper(item.get("reference", ""))
        if ref:
            refs[ref] = item
    return refs


# =========================================================
# COMPARAR UN PAR
# =========================================================

def comparar_par(json_resumen, json_lineas, pdf_resumen, pdf_lineas):
    diferencias = []
    criticas    = []
    avisos      = []

    campos = [
        ("Cliente",  json_resumen["cliente"],  pdf_resumen["cliente"],  False),
        ("Pedido",   json_resumen["pedido"],   pdf_resumen["pedido"],   False),
        ("Tienda",   json_resumen["tienda"],   pdf_resumen["tienda"],   False),
        ("Importe",  json_resumen["importe"],  pdf_resumen["importe"],  True),
        ("IVA",      json_resumen["iva"],      pdf_resumen["iva"],      True),
        ("Total",    json_resumen["total"],    pdf_resumen["total"],    True),
    ]

    for campo, vj, vp, es_num in campos:
        hay_diff = son_numeros_distintos(vj, vp) if es_num else son_textos_distintos(vj, vp)
        if hay_diff:
            diff_str = ""
            if es_num:
                fj = convertir_a_float(vj)
                fp = convertir_a_float(vp)
                if fj is not None and fp is not None:
                    diff_str = a_euro(round(fp - fj, 2))
                criticas.append({"Campo": campo, "JSON": a_euro(vj), "PDF": a_euro(vp), "Diferencia": diff_str, "Qué corregir": f"El {campo} no coincide. Revisar."})
                gravedad = "🔴 Crítico"
            else:
                avisos.append({"Campo": campo, "JSON": vj, "PDF": vp, "Diferencia": "", "Qué corregir": f"El {campo} no coincide. Verificar."})
                gravedad = "🟡 Aviso"
            diferencias.append({"Gravedad": gravedad, "Tipo": "Cabecera", "Campo": campo, "Referencia": "CABECERA",
                                 "Valor JSON": a_euro(vj) if es_num else vj, "Valor PDF": a_euro(vp) if es_num else vp,
                                 "Diferencia": diff_str, "Qué corregir": f"{campo} no coincide."})

    refs_json = indexar_por_referencia(json_lineas)
    refs_pdf  = indexar_por_referencia(pdf_lineas)
    solo_json = sorted(set(refs_json.keys()) - set(refs_pdf.keys()))
    solo_pdf  = sorted(set(refs_pdf.keys())  - set(refs_json.keys()))
    comunes   = sorted(set(refs_json.keys()) & set(refs_pdf.keys()))

    for ref in solo_json:
        criticas.append({"Campo": "Falta en PDF", "JSON": ref, "PDF": "—", "Diferencia": "", "Qué corregir": f"Referencia {ref} en JSON pero no en PDF."})
        diferencias.append({"Gravedad": "🔴 Crítico", "Tipo": "Línea", "Campo": "Solo en JSON", "Referencia": ref,
                             "Valor JSON": ref, "Valor PDF": "", "Diferencia": "", "Qué corregir": f"Referencia {ref} no encontrada en PDF."})

    for ref in solo_pdf:
        avisos.append({"Campo": "Extra en PDF", "JSON": "—", "PDF": ref, "Diferencia": "", "Qué corregir": f"Referencia {ref} en PDF pero no en JSON."})
        diferencias.append({"Gravedad": "🟡 Aviso", "Tipo": "Línea", "Campo": "Solo en PDF", "Referencia": ref,
                             "Valor JSON": "", "Valor PDF": ref, "Diferencia": "", "Qué corregir": f"Referencia {ref} no encontrada en JSON."})

    for ref in comunes:
        j = refs_json[ref]
        p = refs_pdf[ref]

        if son_numeros_distintos(j.get("quantity"), p.get("quantity")):
            avisos.append({"Campo": f"Cantidad — {ref}", "JSON": str(j.get("quantity")), "PDF": str(p.get("quantity")),
                           "Diferencia": "", "Qué corregir": f"Cantidad de {ref}: JSON={j.get('quantity')} PDF={p.get('quantity')}"})
            diferencias.append({"Gravedad": "🟡 Aviso", "Tipo": "Línea", "Campo": "Cantidad", "Referencia": ref,
                                 "Valor JSON": str(j.get("quantity")), "Valor PDF": str(p.get("quantity")),
                                 "Diferencia": "", "Qué corregir": f"Cantidad diferente en {ref}."})

        if son_numeros_distintos(j.get("total_linea"), p.get("importe_linea")):
            fj   = convertir_a_float(j.get("total_linea"))
            fp   = convertir_a_float(p.get("importe_linea"))
            diff = a_euro(round(fp - fj, 2)) if fj is not None and fp is not None else ""
            criticas.append({"Campo": f"Precio — {ref}", "JSON": a_euro(fj), "PDF": a_euro(fp),
                             "Diferencia": diff, "Qué corregir": f"Precio de {ref}: JSON={a_euro(fj)} PDF={a_euro(fp)} Dif={diff}"})
            diferencias.append({"Gravedad": "🔴 Crítico", "Tipo": "Línea", "Campo": "Precio", "Referencia": ref,
                                 "Valor JSON": a_euro(fj), "Valor PDF": a_euro(fp),
                                 "Diferencia": diff, "Qué corregir": f"Precio diferente en {ref}. Dif: {diff}"})

    return diferencias, criticas, avisos


# =========================================================
# MOSTRAR RESULTADO DE UN CLIENTE
# =========================================================

def mostrar_resultado(pedido, cliente, json_resumen, pdf_resumen, diferencias, criticas, avisos):
    n_crit  = len(criticas)
    n_avis  = len(avisos)
    n_total = n_crit + n_avis

    with st.expander(f"📦 Pedido {pedido} — {cliente}", expanded=(n_crit > 0)):
        if n_total == 0:
            st.markdown('<div class="semaforo-verde">✅ TODO CORRECTO</div>', unsafe_allow_html=True)
        elif n_crit > 0:
            st.markdown(f'<div class="semaforo-rojo">🔴 {n_crit} crítica(s) — {n_avis} aviso(s)</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="semaforo-amarillo">🟡 {n_avis} aviso(s) — Revisar</div>', unsafe_allow_html=True)

        c1, c2, c3 = st.columns(3)
        c1.metric("Cliente", json_resumen["cliente"] or pdf_resumen["cliente"] or "—")
        c2.metric("Pedido",  pedido)
        c3.metric("Tienda",  json_resumen["tienda"]  or pdf_resumen["tienda"]  or "—")

        c4, c5, c6 = st.columns(3)
        for col, campo, vj, vp in [(c4, "Importe", json_resumen["importe"], pdf_resumen["importe"]),
                                    (c5, "IVA",     json_resumen["iva"],     pdf_resumen["iva"]),
                                    (c6, "Total",   json_resumen["total"],   pdf_resumen["total"])]:
            fj = convertir_a_float(vj)
            fp = convertir_a_float(vp)
            delta = round(fp - fj, 2) if fj and fp else None
            col.metric(f"{campo} JSON", a_euro(fj), delta=f"{delta} €" if delta else None)

        if criticas:
            st.markdown("#### 🔴 Diferencias críticas")
            for d in criticas:
                st.error(f"**{d['Campo']}** → JSON: `{d['JSON']}` | PDF: `{d['PDF']}` {'| Dif: ' + d['Diferencia'] if d['Diferencia'] else ''}")
                st.caption(f"💡 {d['Qué corregir']}")

        if avisos:
            st.markdown("#### 🟡 Avisos")
            for d in avisos:
                st.warning(f"**{d['Campo']}** → JSON: `{d['JSON']}` | PDF: `{d['PDF']}`")
                st.caption(f"💡 {d['Qué corregir']}")

        if diferencias:
            st.dataframe(pd.DataFrame(diferencias), use_container_width=True, hide_index=True)
            excel = crear_excel_en_memoria(pd.DataFrame(diferencias))
            st.download_button(
                label=f"📥 Descargar Excel — Pedido {pedido}",
                data=excel,
                file_name=f"diferencias_pedido_{pedido}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"excel_{pedido}"
            )


# =========================================================
# INTERFAZ PRINCIPAL
# =========================================================

st.title("📋 Comparador PDF vs JSON — Múltiples clientes")
st.write("Sube todos los JSON y todos los PDF de golpe. La app los empareja automáticamente por número de pedido.")

col1, col2 = st.columns(2)
with col1:
    json_files = st.file_uploader("📂 Archivos JSON", type=["json"], accept_multiple_files=True)
with col2:
    pdf_files  = st.file_uploader("📄 Archivos PDF",  type=["pdf"],  accept_multiple_files=True)

if json_files and pdf_files:
    st.markdown("---")

    # Leer JSONs
    jsons = {}
    for f in json_files:
        try:
            data = json.load(f)
            resumen, lineas = parsear_json(data)
            if resumen["pedido"]:
                jsons[resumen["pedido"]] = (resumen, lineas)
            else:
                st.warning(f"⚠️ {f.name} no tiene número de pedido.")
        except Exception as e:
            st.error(f"Error en {f.name}: {e}")

    # Leer PDFs
    pdfs = {}
    for f in pdf_files:
        try:
            texto    = extraer_texto_pdf(f.read())
            cabecera = parsear_cabecera_pdf(texto)
            importes = extraer_importes_pdf(texto)
            lineas   = parsear_lineas_pdf(texto)
            pedido   = cabecera["pedido"]
            if pedido:
                pdfs[pedido] = ({
                    "pedido":  pedido,
                    "cliente": cabecera["cliente"],
                    "tienda":  cabecera["tienda"],
                    "importe": importes["importe"],
                    "iva":     importes["iva"],
                    "total":   importes["total"],
                }, lineas)
            else:
                st.warning(f"⚠️ {f.name} no tiene número de pedido reconocible.")
        except Exception as e:
            st.error(f"Error en {f.name}: {e}")

    # Emparejar
    emparejados = sorted(set(jsons.keys()) & set(pdfs.keys()))
    sin_pdf     = sorted(set(jsons.keys()) - set(pdfs.keys()))
    sin_json    = sorted(set(pdfs.keys())  - set(jsons.keys()))

    # Resumen general
    st.markdown(f"### 📊 {len(emparejados)} pedido(s) comparado(s)")
    c1, c2, c3 = st.columns(3)
    c1.metric("Pares encontrados", len(emparejados))
    c2.metric("JSON sin PDF",      len(sin_pdf))
    c3.metric("PDF sin JSON",      len(sin_json))

    for p in sin_pdf:
        st.markdown(f'<div class="sin-pareja">⚠️ Pedido <b>{p}</b> — tiene JSON pero no se encontró su PDF</div>', unsafe_allow_html=True)
    for p in sin_json:
        st.markdown(f'<div class="sin-pareja">⚠️ Pedido <b>{p}</b> — tiene PDF pero no se encontró su JSON</div>', unsafe_allow_html=True)

    st.markdown("---")

    # Comparar cada par
    total_crit = 0
    total_avis = 0

    for pedido in emparejados:
        json_resumen, json_lineas = jsons[pedido]
        pdf_resumen,  pdf_lineas  = pdfs[pedido]
        difs, criticas, avisos    = comparar_par(json_resumen, json_lineas, pdf_resumen, pdf_lineas)
        total_crit += len(criticas)
        total_avis += len(avisos)
        mostrar_resultado(pedido, json_resumen["cliente"] or pdf_resumen["cliente"], json_resumen, pdf_resumen, difs, criticas, avisos)

    # Semáforo global
    st.markdown("---")
    st.markdown("### 🚦 Estado general")
    if total_crit > 0:
        st.markdown(f'<div class="semaforo-rojo">🔴 HAY PROBLEMAS — {total_crit} diferencia(s) crítica(s) en total</div>', unsafe_allow_html=True)
    elif total_avis > 0:
        st.markdown(f'<div class="semaforo-amarillo">🟡 REVISAR — {total_avis} aviso(s) en total</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="semaforo-verde">✅ TODOS LOS PEDIDOS CORRECTOS</div>', unsafe_allow_html=True)