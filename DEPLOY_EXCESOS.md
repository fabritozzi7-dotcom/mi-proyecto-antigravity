# Deploy: Control de Rendiciones que Exceden Monto Sugerido

## Pre-requisitos

- Acceso a Google Workspace (Gmail) de Expoconsult
- Acceso al dashboard de Streamlit Cloud
- Acceso a la planilla Google Sheets (SISTEMA_RENDICIONES)

---

## Paso 1: Crear App Password en Gmail

1. Ir a https://myaccount.google.com/apppasswords con la cuenta `alertas@expoconsult.com.ar` (o la que se use para enviar).
2. Si no aparece la opcion, primero habilitar 2FA en https://myaccount.google.com/security.
3. En "App passwords", seleccionar **Mail** y **Other (Custom name)**.
4. Escribir `Expoconsult Rendiciones` como nombre.
5. Click en **Generate**.
6. Copiar la password de 16 caracteres (formato: `xxxx xxxx xxxx xxxx`).
7. NO cerrar la ventana hasta haber pegado la password en Streamlit Cloud.

---

## Paso 2: Configurar Secrets en Streamlit Cloud

1. Ir a https://share.streamlit.io/ → seleccionar la app `expoconsult-rendiciones`.
2. Click en **Settings** → **Secrets**.
3. Agregar el siguiente bloque TOML al final de los secrets existentes:

```toml
[smtp]
user = "alertas@expoconsult.com.ar"
password = "xxxx xxxx xxxx xxxx"
from_name = "Expoconsult Rendiciones"
dry_run = true
```

4. Click **Save**. La app se reinicia automaticamente.

> **IMPORTANTE:** Dejar `dry_run = true` para la primera prueba.

---

## Paso 3: Verificar CONFIG_NOTIFICACIONES en Sheets

1. Abrir la planilla SISTEMA_RENDICIONES en Google Sheets.
2. Verificar que exista la hoja **CONFIG_NOTIFICACIONES**.
   - Si no existe, la app la crea automaticamente en el proximo arranque.
3. Verificar que tenga 4 filas con los destinatarios:

| nombre | email | activo |
|--------|-------|--------|
| VENDRAMINI, CARLA SILVINA | carlavendramini@expoconsult.com.ar | TRUE |
| VENDRAMINI, CONSTANZA ILEANA | constanza@expoconsult.com.ar | TRUE |
| MASTRANGELO, JUAN PABLO | juanpablomastrangelo@expoconsult.com.ar | TRUE |
| MASTRANGELO, TOMAS | tomasmastrangelo@expoconsult.com.ar | TRUE |

---

## Paso 4: Smoke Test en Dry Run

Con `dry_run = true`, hacer las siguientes pruebas:

### Test A: Carga normal (sin exceso)
1. Seleccionar usuario y oficina.
2. Elegir un concepto con monto sugerido (ej: MOVILIDAD A CAMARA, $1,300).
3. Dejar el monto a imputar igual al sugerido.
4. Guardar. **Esperado:** se guarda con estado normal (CERRADO/PENDIENTE/LISTA PARA AJUSTE). No aparece warning ni checkbox. No se envia mail.

### Test B: Carga con exceso
1. Elegir un concepto con monto sugerido (ej: MOVILIDAD A CAMARA, $1,300).
2. Cambiar el monto a imputar a $5,000.
3. **Esperado:** aparece warning amarillo con diferencia ($3,700 / 284.6%).
4. El boton Guardar esta deshabilitado hasta tildar el checkbox.
5. Tildar el checkbox y guardar.
6. **Esperado:** se guarda con estado PENDIENTE REVISION. Aparece mensaje verde.
7. **Verificar logs de Streamlit Cloud:** buscar `SMTP DRY RUN` con el HTML completo del mail.

### Test C: Panel admin
1. Abrir seccion Administracion, ingresar clave.
2. Click en "Cargar pendientes de revision".
3. Verificar que aparece la rendicion del Test B.
4. Aprobar → verificar que el estado cambia a CERRADO/PENDIENTE segun Puchito.
5. Repetir Test B, luego rechazar con motivo → verificar que aparece RECHAZADO en la hoja.

---

## Paso 5: Prueba con Mail Real (solo a vos)

1. En CONFIG_NOTIFICACIONES, poner `activo = FALSE` en las 4 filas existentes.
2. Agregar una fila nueva con tu email y `activo = TRUE`.
3. En Streamlit Cloud Secrets, cambiar `dry_run = false`.
4. Repetir Test B.
5. **Esperado:** te llega el mail con el template HTML.
6. Verificar: asunto, datos de la rendicion, link al comprobante, formato.

---

## Paso 6: Activar en Produccion

1. En CONFIG_NOTIFICACIONES, reactivar las 4 filas originales (`activo = TRUE`).
2. Opcionalmente, quitar tu fila de prueba o dejarla activa.
3. Verificar que `dry_run = false` en secrets.
4. Listo. Las proximas rendiciones con exceso disparan mail a los 4 autorizantes.

---

## Rollback de Emergencia

Si algo sale mal en produccion:

### Opcion A: Desactivar solo el mail
En Streamlit Cloud Secrets, cambiar `dry_run = true`. No requiere deploy.

### Opcion B: Revertir el codigo completo
```bash
# Los 6 commits de la feature (incluyendo el fix de auditoria):
git revert --no-commit HEAD~6..HEAD
git commit -m "Revert: control de excesos (rollback de emergencia)"
git push origin main
```

### Opcion C: Limpiar columnas en Sheets (si hay datos inconsistentes)
1. En RENDICIONES_LOG, las columnas AF-AH (Motivo_Rechazo, Revisado_Por, Fecha_Revision) se pueden borrar o dejar vacias. No afectan el funcionamiento normal ni el export Dux.
2. En CONTROL_SALDOS, si se revertio un saldo incorrectamente, usar "Recalcular Saldos" del panel admin para reconstruir desde RENDICIONES_LOG.
3. La hoja CONFIG_NOTIFICACIONES se puede eliminar o dejar; no afecta nada si el codigo se revierte.

### Verificar post-rollback
- Exportar Dux y verificar que genera el mismo output que antes.
- Cargar una rendicion normal y verificar que guarda con 31 columnas (sin AF-AH).
- El estado filter de Dux vuelve a tener solo los 4 estados originales.
