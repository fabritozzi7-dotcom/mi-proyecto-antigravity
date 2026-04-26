# Deploy: Fix DUX Export

## Pre-requisitos

- Acceso a la planilla Google Sheets (SISTEMA_RENDICIONES)
- Las hojas MAESTRO_CONCEPTOS_DUX, CONFIG_EMPRESA y la columna
  codigo_dux en USUARIOS se crean automaticamente en el primer sync.

---

## Paso 1: Configurar CONFIG_EMPRESA

1. Abrir la hoja **CONFIG_EMPRESA** en Google Sheets.
2. Verificar que existan estas filas:

| clave | valor |
|-------|-------|
| CUIT_EXPOCONSULT | 30570717630 |
| FECHA_INICIO_EXPORT_DUX | 2026-05-01 |

3. **IMPORTANTE:** Cambiar `FECHA_INICIO_EXPORT_DUX` a la fecha real
   del dia en que se despliega el fix (formato YYYY-MM-DD).
   Solo se exportaran rendiciones con fecha >= esta fecha.

---

## Paso 2: Mapear conceptos en MAESTRO_CONCEPTOS_DUX

1. Abrir la hoja **MAESTRO_CONCEPTOS_DUX**.
2. La hoja tiene 59 filas con codigo_dux y nombre_dux ya poblados.
3. Completar la columna **concepto_interno** con el nombre exacto del
   concepto tal como aparece en DB_PARAMETROS.
4. Ejemplo de mapeo:

| concepto_interno | codigo_dux | nombre_dux |
|---|---|---|
| MOVILIDAD A CAMARA | 5150 | MOVILIDAD ADMINISTR.BS.AS. |
| VERIFICACION CANAL ROJO | 5153 | GASTOS VIAJES OFIC BS.AS. |

5. Los conceptos que no tengan mapeo no podran exportarse — la
   validacion pre-export los marcara como error.
6. Si necesitas agregar conceptos DUX nuevos, agrega filas al final
   de la hoja (no borres las existentes).

---

## Paso 3: Mapear empleados en USUARIOS

1. Abrir la hoja **USUARIOS**.
2. La nueva columna **codigo_dux** (D) esta vacia.
3. Completarla con el codigo de cuenta de tesoreria DUX para cada
   usuario que pueda cargar rendiciones.

Referencia de codigos DUX:

| codigo_dux | Nombre en DUX |
|---|---|
| 319 | DAVID REQUELME - CTA RENDICION |
| 320 | PEDRO OVIEDO - CTA RENDICION |
| 322 | CARLOS VALENZUELA - CTA RENDICION |
| 359 | BRENDA FERNANDEZ - CTA RENDICION |
| 361 | FABRICIO DAURIA - CTA.RENDICION |
| 364 | PABLO MUÑOZ - CTA.RENDICION |
| 366 | GABRIEL SALCES - CTA RENDICION |
| 375 | JORGE ANGEL - CTA RENDICION |
| 376 | PABLO ACOSTA - CTA RENDICION |
| 558 | LUCIANO CAMASSA - CTA RENDICIONES |
| 559 | CRISTIAN CALDERON - CTA RENDICION |
| 563 | JUAN PABLO - CTA RENDICION |
| 903 | ALEJANDRO HONORATO - CTA RENDICION |
| 904 | GRACIELA - CTA.RENDICION |
| 920 | GUSTAVO - CTA RENDICION |
| 930 | LUCIANA SARAVIA - CTA RENDICION |

4. Los usuarios sin codigo_dux no podran exportarse — la validacion
   pre-export los marcara como error.
5. No todos los usuarios tienen correspondencia en DUX. Los que no
   tengan, dejar vacio.

---

## Paso 4: Verificar CUIT Cliente en rendiciones

1. A partir del deploy, Gemini extrae automaticamente el CUIT del
   cliente (receptor) de las facturas escaneadas.
2. El operador tiene un campo editable "CUIT del Cliente (Receptor)"
   en el formulario, tanto en modo escaner como manual.
3. Si el CUIT del cliente coincide con el de Expoconsult (30570717630),
   la factura se exporta como PROPIA. Si no, como TERCEROS.
4. Para facturas B/C/Ticket donde no hay CUIT del cliente, se exporta
   como TERCEROS por defecto.

---

## Paso 5: Smoke test

### Test A: Validar con datos existentes
1. Abrir panel admin → seccion Dux export.
2. Click "Validar antes de exportar".
3. Si hay errores (conceptos/empleados sin mapear), corregirlos en
   las hojas correspondientes y re-validar.

### Test B: Factura PROPIA
1. Cargar una rendicion con factura tipo A.
2. En "CUIT del Cliente (Receptor)" poner 30570717630.
3. Guardar.
4. Exportar → verificar que la fila ENC diga "PROPIA" y tenga
   desglose impositivo en columnas O-AB.

### Test C: Factura TERCEROS
1. Cargar una rendicion con factura tipo B.
2. Dejar "CUIT del Cliente" vacio.
3. Guardar.
4. Exportar → verificar que la fila ENC diga "TERCEROS" y las
   columnas O-AB esten vacias.

### Test D: Factura A con CUIT cliente distinto de Expoconsult
1. Cargar factura tipo A con CUIT cliente = cualquier otro CUIT.
2. Exportar → debe salir como TERCEROS (no PROPIA).

---

## Paso 6: Rendiciones historicas

Las rendiciones cargadas antes de este fix NO tienen CUIT cliente
y se tratan asi:
- Se excluyen automaticamente por el cutoff temporal
  (FECHA_INICIO_EXPORT_DUX).
- Si por alguna razon entran al rango de fechas, se exportan como
  TERCEROS (cuit_cliente vacio = TERCEROS).
- Si una factura A historica deberia haber sido PROPIA, el admin
  puede editar la fila en RENDICIONES_LOG y agregar manualmente
  el CUIT cliente en la columna Cuit_Cliente (AI).

---

## Rollback

### Opcion A: Revertir commits
```bash
git revert --no-commit HEAD~6..HEAD
git commit -m "Revert: DUX export fix (rollback)"
git push origin main
```

### Opcion B: Dejar el codigo pero no exportar
Simplemente no usar el boton de exportar hasta resolver los issues.
Las hojas nuevas (MAESTRO_CONCEPTOS_DUX, CONFIG_EMPRESA) no afectan
el funcionamiento normal de la app.

### Hojas que se pueden borrar sin impacto
- MAESTRO_CONCEPTOS_DUX (se recrea en el proximo sync)
- CONFIG_EMPRESA (se recrea en el proximo sync)
- La columna codigo_dux en USUARIOS se puede dejar vacia sin impacto.
