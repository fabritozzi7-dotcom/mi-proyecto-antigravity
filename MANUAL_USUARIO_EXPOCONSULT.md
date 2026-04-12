# Manual de Usuario - Sistema de Gestión de Gastos

## Expoconsult - Guia paso a paso

**Acceso al sistema:** [https://expoconsult-rendiciones.streamlit.app](https://expoconsult-rendiciones.streamlit.app)

**Requisitos:** Solo necesitas un navegador web actualizado (Chrome, Edge, Firefox). No hace falta instalar nada.

> Nota tecnica: La aplicacion corre sobre Python 3.13 y Streamlit Cloud. Si por algun motivo necesitas ejecutarla localmente, asegurate de tener Python 3.13 instalado.

---

## 1. Como subir un comprobante

El sistema permite cargar comprobantes de dos formas:

### Opcion A: Sacar foto con la camara

1. En la seccion **"Comprobante"**, selecciona la pestana **"Camara"**.
2. Tu navegador va a pedir permiso para usar la camara. Acepta.
3. Apunta al ticket o factura y saca la foto.

### Opcion B: Subir un archivo

1. Selecciona la pestana **"Subir"**.
2. Hace clic en **"Seleccionar archivo"**.
3. Elegí una imagen (JPG, PNG) o un PDF del comprobante.

### Escanear con la IA

Una vez que tengas la foto o el archivo cargado, aparece el boton **"Escanear con IA"**. Presionalo y espera unos segundos. La IA (Gemini) va a leer el comprobante y completar automaticamente:

- Tipo de factura (A, B, C)
- CUIT del proveedor
- Punto de venta y numero de comprobante
- Montos (total, neto, IVA, percepciones)

> **Si no tenes comprobante**, marca la casilla **"Cargar sin comprobante / Corregir"** para ingresar los datos a mano.

---

## 2. Como revisar lo que leyo la IA

Despues del escaneo, el sistema muestra una seccion **"Datos del Ticket"** con todo lo que detecto. Es importante revisarlo antes de guardar.

### Que verificar siempre

- **CUIT del Proveedor**: Confirma que el numero sea correcto. Si el proveedor esta en la base de datos, vas a ver un cartel verde que dice "Validado". Si no lo encuentra, te pide que ingreses la Razon Social a mano.
- **Tipo de factura**: Que sea A, B o C segun corresponda.
- **Sucursal y Numero**: Que coincidan con lo impreso en el comprobante.
- **Monto Total**: Que sea igual al total de la factura.

### Atencion especial con Facturas tipo A

Las facturas A discriminan IVA, y la IA desglosa los montos asi:

| Campo | Que significa |
|-------|---------------|
| Neto Gravado | La base antes de impuestos |
| IVA 21% / 10.5% / 27% | Los importes de IVA por alicuota |
| Perc. IVA / Ganancias / IIBB | Percepciones si las hubiera |
| No Gravado | Otros cargos (tasas municipales, impuestos internos, etc.) |

**Como saber si la IA leyo bien una Factura A:**

1. Fijate que el **Neto Gravado multiplicado por 0.21** de un numero parecido al IVA 21% que muestra el sistema. Si no coincide, puede haber multiples alicuotas o cargos extras.
2. El sistema hace una **suma de control**: la suma de todos los montos desglosados tiene que dar igual al Monto Total. Si hay diferencia, vas a ver un cartel amarillo que dice **"Alerta Auditoria"**.
3. Si ves **"Auditoria: Suma de control OK"** en verde, los numeros cuadran.

> **Para Facturas B y C** no se discrimina IVA. Todo el monto va como "No Gravado". No hace falta revisar el desglose.

### Si algo esta mal

Podes corregir cualquier campo directamente en la pantalla: CUIT, tipo de factura, sucursal, numero, y montos. Los campos son editables aunque la IA los haya completado.

---

## 3. Como prorratear un gasto entre varias carpetas

Si un mismo gasto corresponde a mas de una carpeta (por ejemplo, un flete que se reparte entre dos operaciones), el sistema lo divide automaticamente.

### Pasos

1. En el campo **"Numero de Carpeta"**, escribi los codigos separados por coma.
   - Ejemplo: `IMP-2024-001, IMP-2024-002`
2. Completa el resto de los datos como siempre (concepto, monto, comprobante).
3. Al guardar, el sistema divide en partes iguales:
   - Si el ticket es de $10.000 y pones 2 carpetas, registra $5.000 en cada una.
   - El monto a imputar, el neto, y todo el desglose se dividen proporcionalmente.

### Que tener en cuenta

- Se genera **una fila por carpeta** en la planilla de Google Sheets.
- El comprobante (foto/PDF) se sube una sola vez a Drive y se vincula a todas las filas.
- La barra de progreso te muestra el avance mientras guarda cada carpeta.
- Al finalizar, si todo salio bien, el formulario se limpia automaticamente para cargar el siguiente gasto.

---

## 4. Que significa el aviso de "Comprobante Duplicado"

Si al completar el CUIT y el numero de comprobante aparece un cartel amarillo que dice:

> **"Ya existe un comprobante cargado con el mismo CUIT y Numero de Comprobante. No se permite duplicar."**

Significa que ese comprobante ya fue registrado en el sistema anteriormente. Esto es una proteccion para evitar cargar el mismo gasto dos veces por error.

### Que pasa cuando aparece

- El boton **"Guardar Rendicion"** se desactiva (aparece gris y no se puede hacer clic).
- No se puede enviar la rendicion hasta resolver la situacion.

### Que hacer

1. **Verificar si es un error**: Revisa el CUIT y el numero de comprobante. Puede que hayas ingresado datos de otro comprobante por equivocacion. Corregi los campos y el aviso desaparece.
2. **Si efectivamente ya lo cargaste**: No hace falta hacer nada. El gasto ya esta registrado. Podes pasar al siguiente comprobante.
3. **Si necesitas hacer una correccion** sobre un gasto ya cargado, contacta al administrador para que lo modifique directamente en la planilla de Google Sheets.

---

## Resumen rapido del flujo

```
Datos del Operador (fecha, usuario, oficina)
        |
        v
Imputacion (carpeta, operacion, concepto, monto)
        |
        v
Comprobante (foto/archivo + escaneo IA)
        |
        v
Revision de datos (corregir si hace falta)
        |
        v
Guardar Rendicion
```

Ante cualquier duda, consultar con el area de Administracion.

---

*Sistema de Rendiciones Expoconsult - https://expoconsult-rendiciones.streamlit.app*
