def sync_data_from_sheets():
    """
    Connects to GSheets and updates CONCEPTOS_DB and PROVEEDORES_DB.
    Expected Sheet Name: 'SISTEMA_RENDICIONES' (or configurable)
    """
    client = get_gsheets_client()
    if not client:
        return False, "No Client"

    try:
        # Example: Open by name. You should configure this key in .env or secrets
        sheet_name = os.getenv("GSHEET_NAME", "SISTEMA_RENDICIONES")
        sh = client.open(sheet_name)
        
        # 1. DB_PARAMETROS -> Update CONCEPTOS_DB
        try:
            ws_params = sh.worksheet("DB_PARAMETROS")
            rows = ws_params.get_all_values(value_render_option='UNFORMATTED_VALUE')
            
            # Identify headers
            headers = [h.lower().strip() for h in rows[0]]
            try:
                idx_concepto = headers.index("concepto")
                idx_monto = headers.index("monto sugerido")
            except ValueError:
                idx_concepto = 0
                idx_monto = 1
            
            # Try to find 'oficina'
            try:
                idx_oficina = headers.index("oficina")
            except ValueError:
                idx_oficina = -1

            new_conceptos = {}
            new_oficinas = {}

            for row in rows[1:]:
                if len(row) > idx_monto:
                    conc = str(row[idx_concepto]).strip()
                    if not conc: continue 
                    
                    raw_val = row[idx_monto]
                    
                    # Office parsing
                    oficina = "Todas"
                    if idx_oficina != -1 and len(row) > idx_oficina:
                        val_of = str(row[idx_oficina]).strip()
                        if val_of: oficina = val_of

                    if isinstance(raw_val, (int, float)):
                        monto = float(raw_val)
                    else:
                        try:
                            clean_val = str(raw_val).replace("$", "").replace(",", "")
                            monto = float(clean_val)
                        except:
                            monto = 0.0
                    
                    new_conceptos[conc] = monto
                    new_oficinas[conc] = oficina
            
            global CONCEPTOS_DB, CONCEPTOS_OFICINA_DB
            CONCEPTOS_DB.clear()
            CONCEPTOS_DB.update(new_conceptos)
            
            CONCEPTOS_OFICINA_DB.clear()
            CONCEPTOS_OFICINA_DB.update(new_oficinas)
                            
            logger.info("Synced CONCEPTOS_DB from Sheets")
        except Exception as e:
            logger.warning(f"Could not sync DB_PARAMETROS: {e}")

        # 2. DB_PROVEEDORES -> Update PROVEEDORES_DB
        try:
            ws_prov = sh.worksheet("DB_PROVEEDORES")
            rows = ws_prov.get_all_values()
            for row in rows[1:]:
                if len(row) >= 2:
                    cuit = str(row[0]).strip()
                    name = str(row[1]).strip()
                    if cuit and name:
                        PROVEEDORES_DB[cuit] = name
            logger.info(f"Synced {len(rows)-1} providers from Sheets")
        except Exception as e:
             logger.warning(f"Could not sync DB_PROVEEDORES: {e}")

        # 3. DB_CLIENTE -> Update CLIENTES_DB
        try:
            ws_cli = sh.worksheet("DB_CLIENTE")
            rows_cli = ws_cli.get_all_values()
            new_clients = []
            for row in rows_cli[1:]: 
                if len(row) >= 1:
                    cli_name = str(row[0]).strip()
                    if cli_name:
                        new_clients.append(cli_name)
            
            if new_clients:
                global CLIENTES_DB
                CLIENTES_DB.clear()
                CLIENTES_DB.extend(sorted(new_clients))
                logger.info(f"Synced {len(new_clients)} clients from Sheets")
                
        except Exception as e:
            logger.warning(f"Could not sync DB_CLIENTE: {e}")

        return True, "Sync OK"

    except Exception as e:
        logger.error(f"GSheets Sync Error: {e}")
        return False, str(e)
