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

def convertir_a_int(valor):
    """Convierte a int para tamaños (x, y, z). Devuelve None si no es válido."""
    try:
        return int(round(float(str(valor).replace(",", ".").strip())))
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


# Umbral de aviso por precio elevado en una línea
UMBRAL_PRECIO_LINEA = 4000.0


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
        size = item.get("size") or {}
        size_x = convertir_a_int(size.get("x"))
        size_y = convertir_a_int(size.get("y"))
        size_z = convertir_a_int(size.get("z"))

        # Precio de línea: priceTotal preferente, total como fallback
        price_total = convertir_a_float(item.get("priceTotal"))
        if price_total is None:
            price_total = convertir_a_float(item.get("total"))

        lineas.append({
            "id":          limpiar_texto(item.get("id", "")),
            "reference":   limpiar_texto(item.get("reference", "")),
            "name":        limpiar_texto(item.get("name", "")),
            "quantity":    convertir_a_float(item.get("quantity", "")),
            "total_linea": convertir_a_float(item.get("total", "")),
            "observation": limpiar_texto(item.get("observation", "")),
            "opening":     limpiar_texto(item.get("opening", "")),
            "size_x":      size_x,
            "size_y":      size_y,
            "size_z":      size_z,
            "price_total": price_total,
        })
    return resumen, lineas


# =========================================================
# PARSEAR PDF  (parser nuevo: detecta líneas POS reales)
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


# Línea POS típica del PDF: "1 1 ME2P40CX" / "6 14 Complemento" / "3 1 Puerta"
# Estructura: POS(num) CANTIDAD(num) REFERENCIA(resto)
RE_POS_LINEA = re.compile(r"^(\d{1,3})\s+(\d{1,4})\s+(.+)$")


def es_linea_pos(linea):
    """
    Detecta si una línea es la cabecera de una POS de producto.
    Devuelve (pos, cantidad, referencia) o None.
    """
    if linea.upper().startswith("POS MUEBLE"):
        return None
    m = RE_POS_LINEA.match(linea)
    if not m:
        return None
    pos, qty, ref = m.group(1), m.group(2), m.group(3).strip()
    # Descartar si la "referencia" es en realidad un importe
    if re.fullmatch(r"\d+\.\d{2}", ref):
        return None
    return pos, qty, ref


def extraer_datos_bloque_pdf(bloque_texto):
    """Del texto del bloque de una POS, extrae (x, y, z, opening)."""
    size_x = size_y = size_z = None
    opening = ""
    m = re.search(r"L\s*:\s*(\d+)\s*F\s*:\s*(\d+)\s*A\s*:\s*(\d+)", bloque_texto, flags=re.IGNORECASE)
    if m:
        size_x = convertir_a_int(m.group(1))
        size_y = convertir_a_int(m.group(2))
        size_z = convertir_a_int(m.group(3))
    m2 = re.search(r"M\s*:\s*(Izquierda|Derecha)", bloque_texto, flags=re.IGNORECASE)
    if m2:
        opening = m2.group(1).capitalize()
    return size_x, size_y, size_z, opening


def parsear_lineas_pdf(texto):
    """
    Recorre las líneas del PDF. Para cada línea POS detectada,
    agrupa las siguientes líneas hasta la próxima POS (o EOF) y extrae:
      - pos, cantidad, referencia
      - descripcion (primera línea útil)
      - size_x, size_y, size_z
      - opening
      - importe (primer número \\d+\\.\\d{2} del bloque)
    """
    lineas = limpiar_lineas(texto)

    # Localizar índices de líneas POS
    indices_pos = []
    for idx, linea in enumerate(lineas):
        datos = es_linea_pos(linea)
        if datos:
            indices_pos.append((idx, datos))

    resultados = []
    for n, (idx, (pos, qty, ref)) in enumerate(indices_pos):
        fin = indices_pos[n + 1][0] if n + 1 < len(indices_pos) else len(lineas)
        bloque_lineas = lineas[idx + 1: fin]
        bloque_texto  = " ".join(bloque_lineas)

        # Descripción: primera línea útil (no tamaño/opening/variantes/observaciones/importe)
        descripcion = ""
        for l in bloque_lineas:
            if re.match(r"^L\s*:", l, flags=re.IGNORECASE): continue
            if re.match(r"^M\s*:", l, flags=re.IGNORECASE): continue
            if l.startswith("-"): continue
            if l.lower().startswith("observaciones"): continue
            if l.lower().startswith("variantes"): continue
            if re.fullmatch(r"\d+\.\d{2}", l): continue
            descripcion = l
            break

        # Importe: PRIMER número con 2 decimales del bloque
        # (evita coger los totales del pie cuando es la última POS)
        importe = None
        for l in bloque_lineas:
            if re.fullmatch(r"\d+\.\d{2}", l):
                importe = convertir_a_float(l)
                break

        size_x, size_y, size_z, opening = extraer_datos_bloque_pdf(bloque_texto)

        resultados.append({
            "pos":           pos,
            "reference":     limpiar_texto(ref),
            "description":   descripcion,
            "quantity":      convertir_a_float(qty),
            "importe_linea": importe,
            "size_x":        size_x,
            "size_y":        size_y,
            "size_z":        size_z,
            "opening":       opening,
        })
    return resultados


def indexar_por_referencia(lineas):
    """
    Indexa por referencia. Si hay duplicadas (p.ej. ME2P40CX izq y dcha),
    deja la primera — los detalles finos se resuelven en la comparación por ID.
    """
    refs = {}
    for item in lineas:
        ref = limpiar_upper(item.get("reference", ""))
        if ref and ref not in refs:
            refs[ref] = item
    return refs


# Emparejar por id con prioridad: ref+opening → ref+tamaño → solo ref
def emparejar_lineas_por_id(json_lineas, pdf_lineas):
    """
    Devuelve:
      - lista de tuplas (json_line, pdf_line_or_None)
      - lista de líneas PDF que se han quedado sin pareja
    """
    pdf_disponibles = list(pdf_lineas)  # copia
    emparejados = []

    def quitar(pdf_item):
        for idx, p in enumerate(pdf_disponibles):
            if p is pdf_item:
                pdf_disponibles.pop(idx)
                return

    for j in json_lineas:
        ref_j = limpiar_upper(j.get("reference"))
        opn_j = limpiar_upper(j.get("opening"))
        sx, sy, sz = j.get("size_x"), j.get("size_y"), j.get("size_z")

        candidato = None

        # Prioridad 1: referencia + opening
        if opn_j:
            for p in pdf_disponibles:
                if (limpiar_upper(p.get("reference")) == ref_j
                        and limpiar_upper(p.get("opening")) == opn_j):
                    candidato = p
                    break

        # Prioridad 2: referencia + tamaño exacto
        if candidato is None:
            for p in pdf_disponibles:
                if (limpiar_upper(p.get("reference")) == ref_j
                        and p.get("size_x") == sx
                        and p.get("size_y") == sy
                        and p.get("size_z") == sz):
                    candidato = p
                    break

        # Prioridad 3: solo referencia (primero que aparezca)
        if candidato is None:
            for p in pdf_disponibles:
                if limpiar_upper(p.get("reference")) == ref_j:
                    candidato = p
                    break

        emparejados.append((j, candidato))
        if candidato is not None:
            quitar(candidato)

    return emparejados, pdf_disponibles


def formatear_tamano(x, y, z):
    def f(v):
        return str(v) if v is not None else "?"
    return f"{f(x)}×{f(y)}×{f(z)}"


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

    for ref in solo_json:
        criticas.append({"Campo": "Falta en PDF", "JSON": ref, "PDF": "—", "Diferencia": "", "Qué corregir": f"Referencia {ref} en JSON pero no en PDF."})
        diferencias.append({"Gravedad": "🔴 Crítico", "Tipo": "Línea", "Campo": "Solo en JSON", "Referencia": ref,
                             "Valor JSON": ref, "Valor PDF": "", "Diferencia": "", "Qué corregir": f"Referencia {ref} no encontrada en PDF."})

    for ref in solo_pdf:
        avisos.append({"Campo": "Extra en PDF", "JSON": "—", "PDF": ref, "Diferencia": "", "Qué corregir": f"Referencia {ref} en PDF pero no en JSON."})
        diferencias.append({"Gravedad": "🟡 Aviso", "Tipo": "Línea", "Campo": "Solo en PDF", "Referencia": ref,
                             "Valor JSON": "", "Valor PDF": ref, "Diferencia": "", "Qué corregir": f"Referencia {ref} no encontrada en JSON."})

    # Comparación por ID (tamaño, precio por línea y aviso precio elevado)
    comparacion_id, criticas_id, avisos_id, diferencias_id = comparar_por_id(json_lineas, pdf_lineas)
    criticas.extend(criticas_id)
    avisos.extend(avisos_id)
    diferencias.extend(diferencias_id)

    return diferencias, criticas, avisos, comparacion_id


def comparar_por_id(json_lineas, pdf_lineas):
    """
    Devuelve:
      - filas_tabla: lista de dicts (una por id) con Precio JSON, Precio PDF, Dif
      - criticas, avisos, diferencias: para la tabla general y el Excel
    """
    filas_tabla = []
    criticas    = []
    avisos      = []
    diferencias = []

    emparejados, _pdf_huerfanos = emparejar_lineas_por_id(json_lineas, pdf_lineas)

    for j, p in emparejados:
        id_json  = j.get("id") or "—"
        ref      = j.get("reference") or "—"
        nombre   = j.get("name") or "—"
        opening  = j.get("opening") or ""
        tam_json = formatear_tamano(j.get("size_x"), j.get("size_y"), j.get("size_z"))
        precio_j = j.get("price_total")
        precio_j_str = a_euro(precio_j) if precio_j is not None else "—"

        # Aviso de precio elevado (independiente del emparejamiento)
        precio_elevado = precio_j is not None and precio_j > UMBRAL_PRECIO_LINEA

        if p is None:
            # Sin pareja en PDF
            fila = {
                "ID":          id_json,
                "Referencia":  ref,
                "Nombre":      nombre + (f" ({opening})" if opening else ""),
                "Tamaño JSON": tam_json,
                "Tamaño PDF":  "—",
                "Precio JSON": precio_j_str + (" ⚠️" if precio_elevado else ""),
                "Precio PDF":  "—",
                "Dif. precio": "—",
                "Estado":      "🔴 Sin pareja en PDF",
            }
            filas_tabla.append(fila)
            criticas.append({
                "Campo": f"ID {id_json} ({ref})",
                "JSON": f"{ref} {tam_json}",
                "PDF": "—",
                "Diferencia": "",
                "Qué corregir": f"El id {id_json} ({ref}) no se ha emparejado con ninguna línea del PDF.",
            })
            diferencias.append({
                "Gravedad": "🔴 Crítico", "Tipo": "ID",
                "Campo": "Sin pareja PDF", "Referencia": f"{id_json} / {ref}",
                "Valor JSON": tam_json, "Valor PDF": "",
                "Diferencia": "",
                "Qué corregir": f"ID {id_json} ({ref}) no encontrado en el PDF.",
            })
        else:
            tam_pdf = formatear_tamano(p.get("size_x"), p.get("size_y"), p.get("size_z"))
            precio_p = convertir_a_float(p.get("importe_linea"))
            precio_p_str = a_euro(precio_p) if precio_p is not None else "—"

            # Diferencia de precio
            if precio_j is not None and precio_p is not None:
                dif_precio = round(precio_p - precio_j, 2)
                dif_precio_str = a_euro(dif_precio)
            else:
                dif_precio = None
                dif_precio_str = "—"

            # Comparar cada eje
            diffs_ejes = []
            for eje, vj, vp in [
                ("x/L", j.get("size_x"), p.get("size_x")),
                ("y/F", j.get("size_y"), p.get("size_y")),
                ("z/A", j.get("size_z"), p.get("size_z")),
            ]:
                if vj != vp:
                    diffs_ejes.append(f"{eje}: JSON={vj} / PDF={vp}")

            # Estado de la fila
            motivos_rojo = []
            if diffs_ejes:
                motivos_rojo.append("tamaño")
            if dif_precio is not None and dif_precio != 0:
                motivos_rojo.append("precio")

            if motivos_rojo:
                estado = "🔴 " + " + ".join(motivos_rojo).capitalize() + " distinto"
            else:
                estado = "🟢 OK"

            # Diferencias de tamaño → críticas
            if diffs_ejes:
                detalle = " · ".join(diffs_ejes)
                criticas.append({
                    "Campo": f"Tamaño — {id_json} ({ref})",
                    "JSON": tam_json,
                    "PDF": tam_pdf,
                    "Diferencia": detalle,
                    "Qué corregir": f"Tamaño distinto en {ref} (id {id_json}): {detalle}",
                })
                diferencias.append({
                    "Gravedad": "🔴 Crítico", "Tipo": "ID",
                    "Campo": "Tamaño", "Referencia": f"{id_json} / {ref}",
                    "Valor JSON": tam_json, "Valor PDF": tam_pdf,
                    "Diferencia": detalle,
                    "Qué corregir": f"Tamaño distinto en {ref}: {detalle}",
                })

            # Diferencias de precio de línea → críticas
            if dif_precio is not None and dif_precio != 0:
                criticas.append({
                    "Campo": f"Precio línea — {id_json} ({ref})",
                    "JSON": precio_j_str,
                    "PDF": precio_p_str,
                    "Diferencia": dif_precio_str,
                    "Qué corregir": f"Precio de línea {ref} (id {id_json}): JSON={precio_j_str} PDF={precio_p_str} Dif={dif_precio_str}",
                })
                diferencias.append({
                    "Gravedad": "🔴 Crítico", "Tipo": "ID",
                    "Campo": "Precio línea", "Referencia": f"{id_json} / {ref}",
                    "Valor JSON": precio_j_str, "Valor PDF": precio_p_str,
                    "Diferencia": dif_precio_str,
                    "Qué corregir": f"Precio de línea distinto en {ref}.",
                })

            filas_tabla.append({
                "ID":          id_json,
                "Referencia":  ref,
                "Nombre":      nombre + (f" ({opening})" if opening else ""),
                "Tamaño JSON": tam_json,
                "Tamaño PDF":  tam_pdf,
                "Precio JSON": precio_j_str + (" ⚠️" if precio_elevado else ""),
                "Precio PDF":  precio_p_str,
                "Dif. precio": dif_precio_str,
                "Estado":      estado,
            })

        # Aviso por precio > umbral (no depende de si hay pareja o no)
        if precio_elevado:
            avisos.append({
                "Campo": f"Precio elevado — {id_json} ({ref})",
                "JSON": precio_j_str,
                "PDF": "",
                "Diferencia": "",
                "Qué corregir": f"El id {id_json} ({ref}) tiene un precio de {precio_j_str} (> {UMBRAL_PRECIO_LINEA:.0f} €). Revisar posible valor erróneo.",
            })
            diferencias.append({
                "Gravedad": "🟡 Aviso", "Tipo": "ID",
                "Campo": "Precio elevado (>4000 €)", "Referencia": f"{id_json} / {ref}",
                "Valor JSON": precio_j_str, "Valor PDF": "",
                "Diferencia": "",
                "Qué corregir": f"Precio de línea {precio_j_str} supera el umbral de {UMBRAL_PRECIO_LINEA:.0f} €. Revisar.",
            })

    return filas_tabla, criticas, avisos, diferencias


# =========================================================
# MOSTRAR RESULTADO DE UN CLIENTE
# =========================================================

def mostrar_resultado(pedido, cliente, json_resumen, pdf_resumen, diferencias, criticas, avisos, comparacion_id):
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

        # Tabla de comparación por ID
        if comparacion_id:
            st.markdown("#### 🔍 Comparación por ID (tamaño y precio por línea)")
            st.caption(
                "Cada fila corresponde a un `id` del JSON. Se compara el tamaño "
                "(x↔L, y↔F, z↔A) y el precio de línea con el PDF. "
                f"El icono ⚠️ junto al precio indica que supera {UMBRAL_PRECIO_LINEA:.0f} € "
                "(posible valor erróneo)."
            )
            df_id = pd.DataFrame(comparacion_id)
            st.dataframe(df_id, use_container_width=True, hide_index=True)

        if diferencias:
            st.markdown("#### 📋 Tabla completa de diferencias")
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
        difs, criticas, avisos, comparacion_id = comparar_par(json_resumen, json_lineas, pdf_resumen, pdf_lineas)
        total_crit += len(criticas)
        total_avis += len(avisos)
        mostrar_resultado(
            pedido,
            json_resumen["cliente"] or pdf_resumen["cliente"],
            json_resumen, pdf_resumen,
            difs, criticas, avisos,
            comparacion_id,
        )

    # Semáforo global
    st.markdown("---")
    st.markdown("### 🚦 Estado general")
    if total_crit > 0:
        st.markdown(f'<div class="semaforo-rojo">🔴 HAY PROBLEMAS — {total_crit} diferencia(s) crítica(s) en total</div>', unsafe_allow_html=True)
    elif total_avis > 0:
        st.markdown(f'<div class="semaforo-amarillo">🟡 REVISAR — {total_avis} aviso(s) en total</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="semaforo-verde">✅ TODOS LOS PEDIDOS CORRECTOS</div>', unsafe_allow_html=True)