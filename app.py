import streamlit as st
import json
import fitz
import pandas as pd
import re
from io import BytesIO

st.set_page_config(page_title="Comparador JSON vs PDF", layout="wide")

st.title("Comparador JSON vs PDF")
st.write("Sube un archivo JSON y un archivo PDF para detectar solo las diferencias entre uno y otro.")


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


# =========================================================
# JSON
# =========================================================

def parsear_json(data):
    resumen = {
        "cliente": limpiar_texto(data.get("customerName", "")),
        "pedido": limpiar_texto(data.get("orderCode", "")),
        "tienda": limpiar_texto(data.get("storeName", "")),
        "proyecto": limpiar_texto(data.get("projectName", "")),
        "importe": convertir_a_float(data.get("importe", 0)),
        "iva": convertir_a_float(data.get("iva", 0)),
        "total": convertir_a_float(data.get("total", 0)),
    }

    lineas = []
    for item in data.get("cabinets", []):
        lineas.append({
            "reference": limpiar_texto(item.get("reference", "")),
            "name": limpiar_texto(item.get("name", "")),
            "quantity": convertir_a_float(item.get("quantity", "")),
            "total_linea": convertir_a_float(item.get("total", "")),
            "observation": limpiar_texto(item.get("observation", "")),
        })

    return resumen, lineas


# =========================================================
# PDF
# =========================================================

def extraer_texto_pdf(pdf_file):
    pdf_bytes = pdf_file.read()
    pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    texto = ""
    for page in pdf_doc:
        texto += page.get_text() + "\n"

    return len(pdf_doc), texto


def limpiar_lineas(texto):
    return [line.strip() for line in texto.splitlines() if line.strip()]


def parsear_cabecera_pdf(texto):
    """
    Adaptado al formato real mostrado:
    tras 'Cliente:' aparecen:
    pedido
    creado
    envío
    cliente
    tienda
    """
    lineas = limpiar_lineas(texto)

    pedido = ""
    cliente = ""
    tienda = ""

    try:
        idx = lineas.index("Cliente:")
        bloque = lineas[idx + 1: idx + 6]

        if len(bloque) >= 5:
            pedido = limpiar_texto(bloque[0])
            cliente = limpiar_texto(bloque[3])
            tienda = limpiar_texto(bloque[4])
    except ValueError:
        pass

    return {
        "pedido": pedido,
        "cliente": cliente,
        "tienda": tienda,
    }


def extraer_importes_pdf(texto):
    """
    Coge los últimos 3 importes decimales del PDF:
    IMPORTE, IVA, TOTAL
    """
    lineas = limpiar_lineas(texto)
    numeros = []

    for linea in lineas:
        if re.fullmatch(r"\d+\.\d{2}", linea):
            numeros.append(linea)

    if len(numeros) >= 3:
        return {
            "importe": convertir_a_float(numeros[-3]),
            "iva": convertir_a_float(numeros[-2]),
            "total": convertir_a_float(numeros[-1]),
        }

    return {
        "importe": None,
        "iva": None,
        "total": None,
    }


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
    lineas = limpiar_lineas(texto)
    resultados = []

    i = 0
    while i < len(lineas):
        actual = lineas[i]

        if es_referencia_valida(actual):
            referencia = actual
            descripcion = ""
            cantidad = None
            importe = None

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
                "reference": limpiar_texto(referencia),
                "description": descripcion,
                "quantity": cantidad,
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


# =========================================================
# COMPARACIÓN
# =========================================================

def indexar_por_referencia(lineas):
    refs = {}
    for item in lineas:
        ref = limpiar_upper(item.get("reference", ""))
        if ref:
            refs[ref] = item
    return refs


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
# SUBIDA DE ARCHIVOS
# =========================================================

json_file = st.file_uploader("Subir archivo JSON", type=["json"])
pdf_file = st.file_uploader("Subir archivo PDF", type=["pdf"])

json_resumen = None
json_lineas = []
pdf_resumen = None
pdf_lineas = []
pdf_texto = ""


# =========================================================
# PREVISUALIZACIÓN JSON
# =========================================================

if json_file is not None:
    data = json.load(json_file)
    json_resumen, json_lineas = parsear_json(data)

    st.subheader("Resumen del JSON")

    col1, col2 = st.columns(2)

    with col1:
        st.write(f"Cliente: {json_resumen['cliente']}")
        st.write(f"Pedido: {json_resumen['pedido']}")
        st.write(f"Tienda: {json_resumen['tienda']}")
        st.write(f"Proyecto: {json_resumen['proyecto']}")
        st.write(f"Nº líneas: {len(json_lineas)}")

    with col2:
        st.write(f"Importe JSON: {a_euro(json_resumen['importe'])}")
        st.write(f"IVA JSON: {a_euro(json_resumen['iva'])}")
        st.write(f"Total JSON: {a_euro(json_resumen['total'])}")

    if json_lineas:
        st.subheader("Primeras líneas del JSON")
        st.dataframe(pd.DataFrame(json_lineas[:10]), use_container_width=True)


# =========================================================
# PREVISUALIZACIÓN PDF
# =========================================================

if pdf_file is not None:
    total_pages, pdf_texto = extraer_texto_pdf(pdf_file)
    cabecera_pdf = parsear_cabecera_pdf(pdf_texto)
    importes_pdf = extraer_importes_pdf(pdf_texto)
    pdf_lineas = parsear_lineas_pdf(pdf_texto)

    pdf_resumen = {
        "cliente": cabecera_pdf["cliente"],
        "pedido": cabecera_pdf["pedido"],
        "tienda": cabecera_pdf["tienda"],
        "importe": importes_pdf["importe"],
        "iva": importes_pdf["iva"],
        "total": importes_pdf["total"],
        "paginas": total_pages,
    }

    st.subheader("Resumen del PDF")

    col1, col2 = st.columns(2)

    with col1:
        st.write(f"Páginas PDF: {pdf_resumen['paginas']}")
        st.write(f"Cliente PDF: {pdf_resumen['cliente']}")
        st.write(f"Pedido PDF: {pdf_resumen['pedido']}")
        st.write(f"Tienda PDF: {pdf_resumen['tienda']}")

    with col2:
        st.write(f"Importe PDF: {a_euro(pdf_resumen['importe'])}")
        st.write(f"IVA PDF: {a_euro(pdf_resumen['iva'])}")
        st.write(f"Total PDF: {a_euro(pdf_resumen['total'])}")
        st.write(f"Líneas detectadas PDF: {len(pdf_lineas)}")

    st.subheader("Vista previa PDF")
    st.text_area("Texto extraído", pdf_texto[:5000], height=350)

    if pdf_lineas:
        st.subheader("Líneas detectadas en PDF")
        st.dataframe(pd.DataFrame(pdf_lineas), use_container_width=True)


# =========================================================
# BOTÓN COMPARAR
# =========================================================

if st.button("Comparar"):
    if json_resumen is None or pdf_resumen is None:
        st.error("Debes subir el JSON y el PDF antes de comparar.")
    else:
        diferencias = []

        st.subheader("Diferencias detectadas entre JSON y PDF")

        # -------------------------
        # CABECERA: solo diferencias
        # -------------------------
        if son_textos_distintos(json_resumen["cliente"], pdf_resumen["cliente"]):
            st.error(
                f"Cliente no coincide → JSON: {json_resumen['cliente']} | PDF: {pdf_resumen['cliente']}"
            )
            diferencias.append({
                "tipo": "Cabecera",
                "campo": "Cliente",
                "referencia": "",
                "valor_json": json_resumen["cliente"],
                "valor_pdf": pdf_resumen["cliente"],
                "diferencia": ""
            })

        if son_textos_distintos(json_resumen["pedido"], pdf_resumen["pedido"]):
            st.error(
                f"Pedido no coincide → JSON: {json_resumen['pedido']} | PDF: {pdf_resumen['pedido']}"
            )
            diferencias.append({
                "tipo": "Cabecera",
                "campo": "Pedido",
                "referencia": "",
                "valor_json": json_resumen["pedido"],
                "valor_pdf": pdf_resumen["pedido"],
                "diferencia": ""
            })

        if son_textos_distintos(json_resumen["tienda"], pdf_resumen["tienda"]):
            st.error(
                f"Tienda no coincide → JSON: {json_resumen['tienda']} | PDF: {pdf_resumen['tienda']}"
            )
            diferencias.append({
                "tipo": "Cabecera",
                "campo": "Tienda",
                "referencia": "",
                "valor_json": json_resumen["tienda"],
                "valor_pdf": pdf_resumen["tienda"],
                "diferencia": ""
            })

        if son_numeros_distintos(json_resumen["importe"], pdf_resumen["importe"]):
            diferencia = None
            if json_resumen["importe"] is not None and pdf_resumen["importe"] is not None:
                diferencia = round(pdf_resumen["importe"] - json_resumen["importe"], 2)

            st.error(
                f"Estos importes no coinciden → "
                f"JSON: {a_euro(json_resumen['importe'])} | PDF: {a_euro(pdf_resumen['importe'])} | "
                f"Diferencia: {a_euro(diferencia)}"
            )
            diferencias.append({
                "tipo": "Cabecera",
                "campo": "Importe",
                "referencia": "CABECERA",
                "valor_json": a_euro(json_resumen["importe"]),
                "valor_pdf": a_euro(pdf_resumen["importe"]),
                "diferencia": a_euro(diferencia)
            })

        if son_numeros_distintos(json_resumen["iva"], pdf_resumen["iva"]):
            diferencia = None
            if json_resumen["iva"] is not None and pdf_resumen["iva"] is not None:
                diferencia = round(pdf_resumen["iva"] - json_resumen["iva"], 2)

            st.error(
                f"Estos IVAs no coinciden → "
                f"JSON: {a_euro(json_resumen['iva'])} | PDF: {a_euro(pdf_resumen['iva'])} | "
                f"Diferencia: {a_euro(diferencia)}"
            )
            diferencias.append({
                "tipo": "Cabecera",
                "campo": "IVA",
                "referencia": "CABECERA",
                "valor_json": a_euro(json_resumen["iva"]),
                "valor_pdf": a_euro(pdf_resumen["iva"]),
                "diferencia": a_euro(diferencia)
            })

        if son_numeros_distintos(json_resumen["total"], pdf_resumen["total"]):
            diferencia = None
            if json_resumen["total"] is not None and pdf_resumen["total"] is not None:
                diferencia = round(pdf_resumen["total"] - json_resumen["total"], 2)

            st.error(
                f"Estos totales no coinciden → "
                f"JSON: {a_euro(json_resumen['total'])} | PDF: {a_euro(pdf_resumen['total'])} | "
                f"Diferencia: {a_euro(diferencia)}"
            )
            diferencias.append({
                "tipo": "Cabecera",
                "campo": "Total",
                "referencia": "CABECERA",
                "valor_json": a_euro(json_resumen["total"]),
                "valor_pdf": a_euro(pdf_resumen["total"]),
                "diferencia": a_euro(diferencia)
            })

        # -------------------------
        # LÍNEAS: solo diferencias
        # -------------------------
        st.subheader("Diferencias por líneas")

        refs_json = indexar_por_referencia(json_lineas)
        refs_pdf = indexar_por_referencia(pdf_lineas)

        solo_json = sorted(set(refs_json.keys()) - set(refs_pdf.keys()))
        solo_pdf = sorted(set(refs_pdf.keys()) - set(refs_json.keys()))
        comunes = sorted(set(refs_json.keys()) & set(refs_pdf.keys()))

        for ref in solo_json:
            st.error(f"Referencia solo en JSON → {ref}")
            diferencias.append({
                "tipo": "Línea",
                "campo": "Referencia solo en JSON",
                "referencia": ref,
                "valor_json": ref,
                "valor_pdf": "",
                "diferencia": ""
            })

        for ref in solo_pdf:
            st.error(f"Referencia solo en PDF → {ref}")
            diferencias.append({
                "tipo": "Línea",
                "campo": "Referencia solo en PDF",
                "referencia": ref,
                "valor_json": "",
                "valor_pdf": ref,
                "diferencia": ""
            })

        for ref in comunes:
            j = refs_json[ref]
            p = refs_pdf[ref]

            if son_numeros_distintos(j.get("quantity"), p.get("quantity")):
                st.error(
                    f"Cantidad no coincide en referencia {ref} → "
                    f"JSON: {j.get('quantity')} | PDF: {p.get('quantity')}"
                )
                diferencias.append({
                    "tipo": "Línea",
                    "campo": "Cantidad",
                    "referencia": ref,
                    "valor_json": j.get("quantity"),
                    "valor_pdf": p.get("quantity"),
                    "diferencia": ""
                })

            if son_numeros_distintos(j.get("total_linea"), p.get("importe_linea")):
                diferencia = None
                jv = convertir_a_float(j.get("total_linea"))
                pv = convertir_a_float(p.get("importe_linea"))
                if jv is not None and pv is not None:
                    diferencia = round(pv - jv, 2)

                st.error(
                    f"Estos precios no coinciden en la referencia {ref} → "
                    f"Precio JSON: {a_euro(j.get('total_linea'))} | "
                    f"Precio PDF: {a_euro(p.get('importe_linea'))} | "
                    f"Diferencia: {a_euro(diferencia)}"
                )
                diferencias.append({
                    "tipo": "Línea",
                    "campo": "Precio línea",
                    "referencia": ref,
                    "valor_json": a_euro(j.get("total_linea")),
                    "valor_pdf": a_euro(p.get("importe_linea")),
                    "diferencia": a_euro(diferencia)
                })

        # -------------------------
        # RESUMEN FINAL
        # -------------------------
        st.subheader("Resumen final")

        if diferencias:
            st.error(f"Se han detectado {len(diferencias)} diferencias entre JSON y PDF.")
            df_diferencias = pd.DataFrame(diferencias)
            st.dataframe(df_diferencias, use_container_width=True)

            excel_bytes = crear_excel_en_memoria(df_diferencias)
            st.download_button(
                label="Descargar Excel con diferencias",
                data=excel_bytes,
                file_name="diferencias_json_vs_pdf.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.success("No se han detectado diferencias entre JSON y PDF.")