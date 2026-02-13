"""
Dashboard Admin Module per S4all Bot
====================================
Gestisce tutti gli endpoint Flask per il pannello admin:
- Dashboard ML Training
- API per correzioni e retraining
- Export dati
- Bot controls (tags, ordini, FAQ, listino)
"""

import os
import json
import logging
import html as html_lib
from datetime import datetime
from flask import request, make_response, send_file
import asyncio

# Import database module
import database as db
from enhanced_logging import classification_logger

logger = logging.getLogger(__name__)

# Riferimenti esterni (verranno impostati da main.py)
_main_app = None
_classifier_instance = None

# Funzioni esterne da main.py (verranno impostate da register_dashboard_routes)
load_user_tags_simple = None
get_ordini_oggi = None
update_faq_from_web = None
load_faq = None
update_lista_from_web = None
estrai_parole_chiave_lista = None
PAROLE_CHIAVE_LISTA = set()


def register_dashboard_routes(app, classifier_ref, **kwargs):
    """
    Registra tutte le route del dashboard sull'app Flask.
    
    Args:
        app: L'istanza Flask
        classifier_ref: Riferimento al classifier instance
        **kwargs: Funzioni e variabili necessarie da main.py
    """
    global _main_app, _classifier_instance
    global load_user_tags_simple, get_ordini_oggi, update_faq_from_web
    global load_faq, update_lista_from_web, estrai_parole_chiave_lista, PAROLE_CHIAVE_LISTA
    
    _main_app = app
    _classifier_instance = classifier_ref
    
    # Estrai funzioni e variabili da kwargs
    load_user_tags_simple = kwargs.get('load_user_tags_simple')
    get_ordini_oggi = kwargs.get('get_ordini_oggi')
    update_faq_from_web = kwargs.get('update_faq_from_web')
    load_faq = kwargs.get('load_faq')
    update_lista_from_web = kwargs.get('update_lista_from_web')
    estrai_parole_chiave_lista = kwargs.get('estrai_parole_chiave_lista')
    PAROLE_CHIAVE_LISTA = kwargs.get('PAROLE_CHIAVE_LISTA', set())
    
    # Registra tutte le route
    _register_routes(app)
    
    logger.info("✅ Dashboard routes registrate")


def _check_auth():
    """Verifica il token di autenticazione admin."""
    auth_token = request.args.get('token')
    return auth_token == os.environ.get('ADMIN_TOKEN', 'S4all')


def _register_routes(app):
    """Registra tutte le route del dashboard."""
    
    # ========================================
    # DASHBOARD PRINCIPALE
    # ========================================
    
    @app.route('/admin/stats', methods=['GET'])
    def admin_stats():
        """Dashboard interattiva per correzione classificazioni"""
        if not _check_auth():
            return {"error": "Unauthorized"}, 401
        
        auth_token = request.args.get('token')
        
        # Recupera tutti i messaggi classificati (ultimi 100) dal database
        cases = db.get_recent_classifications(limit=100)
        stats = classification_logger.get_stats()
        feedback_stats = db.get_feedback_stats()
        
        # Lista intent disponibili
        available_intents = ['order', 'search', 'faq', 'list', 'saluto', 'order_confirmation', 'fallback', 'fallback_mute']
        
        html = _render_dashboard_html(auth_token, cases, stats, feedback_stats, available_intents)
        return html

    # ========================================
    # API CORREZIONI
    # ========================================

    @app.route('/admin/api/correct', methods=['POST'])
    def admin_api_correct():
        """API per salvare correzione da dashboard"""
        if not _check_auth():
            return {"success": False, "message": "Unauthorized"}, 401
        
        try:
            data = request.get_json()
            classification_id = data.get('id')
            text = data.get('text')
            predicted_intent = data.get('predicted_intent')
            correct_intent = data.get('correct_intent')
            is_correct = data.get('is_correct', False)
            
            if not all([text, predicted_intent, correct_intent]):
                return {"success": False, "message": "Dati mancanti"}, 400
            
            success = db.save_classification_feedback(
                original_text=text,
                predicted_intent=predicted_intent,
                correct_intent=correct_intent,
                classification_id=classification_id
            )
            
            if success:
                action = "confermato come corretto" if is_correct else f"corretto in {correct_intent}"
                logger.info(f"✅ Feedback {action}: ID={classification_id} '{text[:40]}...'")
                return {"success": True, "message": "Correzione salvata"}
            else:
                return {"success": False, "message": "Errore database"}, 500
                
        except Exception as e:
            logger.error(f"❌ Errore API correct: {e}")
            return {"success": False, "message": str(e)}, 500

    @app.route('/admin/download-model', methods=['GET'])
    def admin_download_model():
        """Scarica il modello ML aggiornato"""
        if not _check_auth():
            return {"error": "Unauthorized"}, 401
        
        model_path = 'intent_classifier_model.pkl'
        
        if not os.path.exists(model_path):
            return {"error": "Modello non trovato"}, 404
        
        return send_file(
            model_path,
            mimetype='application/octet-stream',
            as_attachment=True,
            download_name=f'intent_classifier_model_{datetime.now().strftime("%Y%m%d_%H%M%S")}.pkl'
        )

    @app.route('/admin/api/retrain', methods=['POST'])
    def admin_api_retrain():
        """API per forzare retraining manuale dalla dashboard"""
        if not _check_auth():
            return {"success": False, "message": "Unauthorized"}, 401
        
        try:
            from feedback_handler import get_retraining_status, trigger_retraining
            
            status = get_retraining_status()
            if not status['can_retrain']:
                return {
                    "success": False, 
                    "message": f"Feedback insufficienti. Hai {status['feedback_pending']}, servono 10."
                }, 400
            
            result = trigger_retraining()
            return result
            
        except Exception as e:
            logger.error(f"❌ Errore API retrain: {e}")
            return {"success": False, "message": str(e)}, 500

    # ========================================
    # EXPORT DATI
    # ========================================

    @app.route('/admin/export', methods=['GET'])
    def admin_export():
        """Export low confidence cases per retraining"""
        if not _check_auth():
            return {"error": "Unauthorized"}, 401
        
        output_file = classification_logger.export_for_retraining()
        
        if output_file and os.path.exists(output_file):
            with open(output_file, 'r') as f:
                data = json.load(f)
            return {"exported": len(data), "cases": data}, 200
        
        return {"error": "Export failed"}, 500

    @app.route('/admin/intent/<intent_name>', methods=['GET'])
    def admin_intent_detail(intent_name):
        """Analisi dettagliata di un intent specifico"""
        if not _check_auth():
            return {"error": "Unauthorized"}, 401
        
        auth_token = request.args.get('token')
        
        # Ottieni distribuzione confidence
        distribution = classification_logger.get_confidence_distribution(intent_name)
        
        if not distribution:
            return f"<h1>Intent '{intent_name}' non trovato</h1>", 404
        
        # Ottieni tutti i casi per questo intent
        cases = classification_logger.get_cases_by_intent(intent_name, limit=100)
        
        return _render_intent_detail_html(auth_token, intent_name, distribution, cases)

    @app.route('/admin/export_intent/<intent_name>', methods=['GET'])
    def admin_export_intent(intent_name):
        """
        Export JSON completo per un intent specifico
        Include campo 'correct_intent' vuoto per correzioni manuali
        """
        if not _check_auth():
            return {"error": "Unauthorized"}, 401
        
        # Usa il nuovo metodo che include correct_intent
        export_data = classification_logger.export_intent_for_correction(intent_name, limit=1000)
        
        if "error" in export_data:
            return export_data, 404
        
        # Ritorna JSON con headers per download
        response = make_response(json.dumps(export_data, indent=2, ensure_ascii=False))
        response.headers['Content-Type'] = 'application/json; charset=utf-8'
        response.headers['Content-Disposition'] = f'attachment; filename=intent_{intent_name}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
        
        return response

    @app.route('/admin/trends', methods=['GET'])
    def admin_trends():
        """Dashboard trend storici mensili"""
        if not _check_auth():
            return {"error": "Unauthorized"}, 401
        
        auth_token = request.args.get('token')
        months = int(request.args.get('months', 6))
        trends = db.get_monthly_trends(months)
        
        if not trends:
            return "<h1>Nessun trend storico disponibile</h1>", 404
        
        return _render_trends_html(auth_token, months, trends)

    # ========================================
    # API BOT CONTROLS
    # ========================================

    @app.route('/admin/api/tags', methods=['GET'])
    def admin_api_tags():
        """API per ottenere tutti i tag clienti"""
        if not _check_auth():
            return {"success": False, "message": "Unauthorized"}, 401

        try:
            tags = load_user_tags_simple()
            tags_list = [
                {"user_id": uid, "tag": tag}
                for uid, tag in tags.items()
            ]
            return {"success": True, "tags": tags_list, "count": len(tags_list)}
        except Exception as e:
            logger.error(f"❌ Errore API tags: {e}")
            return {"success": False, "message": str(e)}, 500

    @app.route('/admin/api/ordini', methods=['GET'])
    def admin_api_ordini():
        """API per ottenere gli ordini di oggi"""
        if not _check_auth():
            return {"success": False, "message": "Unauthorized"}, 401

        try:
            ordini = get_ordini_oggi()
            return {"success": True, "ordini": ordini, "count": len(ordini)}
        except Exception as e:
            logger.error(f"❌ Errore API ordini: {e}")
            return {"success": False, "message": str(e)}, 500

    @app.route('/admin/api/aggiorna-faq', methods=['POST'])
    def admin_api_aggiorna_faq():
        """API per aggiornare le FAQ da JustPaste"""
        if not _check_auth():
            return {"success": False, "message": "Unauthorized"}, 401

        try:
            # Esegui in un thread separato con il proprio loop isolato
            import threading
            import queue
            
            result_queue = queue.Queue()
            
            def run_async_in_thread():
                """Esegue la coroutine in un thread con il proprio loop"""
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    result = loop.run_until_complete(update_faq_from_web())
                    result_queue.put(('success', result))
                except Exception as e:
                    result_queue.put(('error', str(e)))
                finally:
                    loop.close()
            
            thread = threading.Thread(target=run_async_in_thread)
            thread.start()
            thread.join(timeout=30)  # Timeout 30 secondi
            
            if thread.is_alive():
                return {"success": False, "message": "Timeout durante aggiornamento FAQ"}, 504
            
            status, result = result_queue.get()
            
            if status == 'error':
                return {"success": False, "message": result}, 500
            
            if result:
                faq_data = load_faq()
                count = len(faq_data.get("faq", []))
                return {"success": True, "message": f"FAQ aggiornate: {count} elementi"}
            return {"success": False, "message": "Errore durante aggiornamento FAQ"}, 500
        except Exception as e:
            logger.error(f"❌ Errore API aggiorna-faq: {e}")
            return {"success": False, "message": str(e)}, 500

    @app.route('/admin/api/aggiorna-lista', methods=['POST'])
    def admin_api_aggiorna_lista():
        """API per aggiornare il listino da JustPaste"""
        if not _check_auth():
            return {"success": False, "message": "Unauthorized"}, 401

        try:
            result = update_lista_from_web()

            if result:
                global PAROLE_CHIAVE_LISTA, _classifier_instance
                PAROLE_CHIAVE_LISTA = estrai_parole_chiave_lista()

                if _classifier_instance:
                    _classifier_instance.product_keywords = list(PAROLE_CHIAVE_LISTA)

                return {
                    "success": True,
                    "message": f"Listino aggiornato. {len(PAROLE_CHIAVE_LISTA)} keywords estratte."
                }
            return {"success": False, "message": "Errore durante aggiornamento listino"}, 500
        except Exception as e:
            logger.error(f"❌ Errore API aggiorna-lista: {e}")
            return {"success": False, "message": str(e)}, 500


def _render_dashboard_html(auth_token, cases, stats, feedback_stats, available_intents):
    """Renderizza l'HTML della dashboard principale."""
    
    model_info = ""
    if os.path.exists('intent_classifier_model.pkl'):
        mtime = datetime.fromtimestamp(os.path.getmtime('intent_classifier_model.pkl'))
        model_info = f"📅 Ultimo aggiornamento: {mtime.strftime('%d/%m/%Y %H:%M')}"
    else:
        model_info = "⚠️ Modello non trovato"
    
    backup_count = 0
    if os.path.exists('training/backups'):
        backup_count = len([f for f in os.listdir('training/backups') if f.startswith('model_backup_')])
    
    retraining_ready = feedback_stats['pending'] >= 10
    retraining_text = "Pronto per retraining!" if retraining_ready else f"Servono altri {10 - feedback_stats['pending']} feedback per il retraining automatico"
    retraining_btn = '<button class="save-btn" onclick="forceRetrain()">🚀 Forza Retraining</button>' if retraining_ready else ''
    
    # Genera righe tabella
    rows_html = ""
    for case in cases:
        conf = case['confidence']
        conf_class = 'conf-high' if conf >= 0.85 else 'conf-medium' if conf >= 0.70 else 'conf-low'
        intent_class = f"intent-{case['intent']}"
        safe_text = html_lib.escape(case['text'], quote=True)
        text_display = case['text'][:100] + ('...' if len(case['text']) > 100 else '')
        
        options_html = ''.join([f'<option value="{intent}">{intent}</option>' for intent in available_intents if intent != case['intent']])
        
        rows_html += f"""
                        <tr id="row-{case['id']}" data-intent="{case['intent']}" data-confidence="{conf}" data-full-text="{safe_text}">
                            <td>#{case['id']}</td>
                            <td class="message-text">{text_display}</td>
                            <td><span class="intent-badge {intent_class}">{case['intent']}</span></td>
                            <td><span class="confidence {conf_class}">{conf:.2f}</span></td>
                            <td>
                                <select class="correction-select" id="select-{case['id']}" onchange="enableSave({case['id']})">
                                    <option value="">-- Seleziona --</option>
                                    {options_html}
                                </select>
                            </td>
                            <td>
                                <button class="save-btn" id="btn-{case['id']}" onclick="saveCorrection({case['id']})" disabled>
                                    Salva
                                </button>
                                <button class="correct-btn" id="correct-btn-{case['id']}" onclick="markCorrect({case['id']})" title="Segna come corretto">
                                    ✓ Corretta
                                </button>
                            </td>
                        </tr>
        """
    
    intent_options = ''.join([f'<option value="{intent}">{intent}</option>' for intent in available_intents])
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Bot ML Training Dashboard</title>
        <meta charset="UTF-8">
        <style>
            * {{ box-sizing: border-box; }}
            body {{ 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; 
                margin: 0; 
                padding: 20px; 
                background: #0f1117; 
                color: #e1e4e8;
            }}
            .header {{ 
                background: linear-gradient(135deg, #1a1e2e 0%, #2d1b4e 100%); 
                color: white; 
                padding: 30px; 
                border-radius: 16px; 
                margin-bottom: 24px;
                box-shadow: 0 8px 32px rgba(0,0,0,0.4);
                border: 1px solid rgba(255,255,255,0.06);
            }}
            .header h1 {{ margin: 0; font-size: 28px; }}
            .stats-bar {{ 
                display: flex; 
                gap: 16px; 
                margin-top: 15px;
                flex-wrap: wrap;
            }}
            .stat-box {{ 
                background: rgba(255,255,255,0.08); 
                padding: 15px 25px; 
                border-radius: 10px;
                backdrop-filter: blur(10px);
                border: 1px solid rgba(255,255,255,0.05);
            }}
            .stat-value {{ font-size: 24px; font-weight: bold; }}
            .stat-label {{ font-size: 12px; opacity: 0.8; }}
            
            .container {{ max-width: 1400px; margin: 0 auto; }}
            
            /* === TAB NAVIGATION === */
            .tab-nav {{
                display: flex;
                gap: 4px;
                margin-top: 20px;
            }}
            .tab-btn {{
                padding: 10px 24px;
                border: none;
                border-radius: 8px 8px 0 0;
                cursor: pointer;
                font-size: 14px;
                font-weight: 600;
                transition: all 0.25s ease;
                background: rgba(255,255,255,0.06);
                color: rgba(255,255,255,0.5);
            }}
            .tab-btn:hover {{
                background: rgba(255,255,255,0.12);
                color: rgba(255,255,255,0.8);
            }}
            .tab-btn.active {{
                background: rgba(102,126,234,0.25);
                color: #a8b8ff;
                border-bottom: 2px solid #667eea;
            }}
            .tab-panel {{
                display: none;
                animation: fadeIn 0.3s ease;
            }}
            .tab-panel.active {{
                display: block;
            }}
            @keyframes fadeIn {{
                from {{ opacity: 0; transform: translateY(8px); }}
                to {{ opacity: 1; transform: translateY(0); }}
            }}
            
            .filters {{ 
                background: #161b22; 
                padding: 20px; 
                border-radius: 12px; 
                margin-bottom: 20px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.2);
                display: flex;
                gap: 15px;
                align-items: center;
                flex-wrap: wrap;
                border: 1px solid #30363d;
            }}
            .filters label {{ font-weight: 600; color: #8b949e; }}
            .filters select, .filters input {{
                padding: 10px 15px;
                border: 1px solid #30363d;
                border-radius: 8px;
                font-size: 14px;
                min-width: 150px;
                background: #0d1117;
                color: #e1e4e8;
            }}
            .filters select:focus, .filters input:focus {{
                outline: none;
                border-color: #667eea;
                box-shadow: 0 0 0 3px rgba(102,126,234,0.15);
            }}
            
            .messages-table {{
                background: #161b22;
                border-radius: 12px;
                overflow: hidden;
                box-shadow: 0 2px 8px rgba(0,0,0,0.2);
                border: 1px solid #30363d;
            }}
            table {{ 
                width: 100%; 
                border-collapse: collapse; 
            }}
            th {{ 
                background: #1c2129; 
                padding: 15px; 
                text-align: left; 
                font-weight: 600; 
                color: #8b949e;
                border-bottom: 1px solid #30363d;
                position: sticky;
                top: 0;
            }}
            td {{ 
                padding: 15px; 
                border-bottom: 1px solid #21262d;
                vertical-align: middle;
                color: #c9d1d9;
            }}
            tr:hover {{ background: rgba(255,255,255,0.03); }}
            
            .message-text {{ 
                max-width: 400px; 
                word-break: break-word;
                font-family: 'JetBrains Mono', 'Fira Code', monospace;
                font-size: 13px;
                background: #0d1117;
                padding: 8px 12px;
                border-radius: 6px;
                color: #c9d1d9;
                border: 1px solid #21262d;
            }}
            
            .intent-badge {{
                display: inline-block;
                padding: 6px 12px;
                border-radius: 20px;
                font-size: 12px;
                font-weight: 600;
                text-transform: uppercase;
            }}
            .intent-order {{ background: rgba(56,132,244,0.15); color: #58a6ff; }}
            .intent-search {{ background: rgba(188,140,255,0.15); color: #bc8cff; }}
            .intent-faq {{ background: rgba(63,185,80,0.15); color: #3fb950; }}
            .intent-list {{ background: rgba(210,153,34,0.15); color: #d29922; }}
            .intent-fallback {{ background: rgba(248,81,73,0.15); color: #f85149; }}
            .intent-saluto {{ background: rgba(56,203,204,0.15); color: #39d1d2; }}
            
            .confidence {{
                font-weight: 600;
                padding: 4px 8px;
                border-radius: 4px;
            }}
            .conf-high {{ color: #3fb950; background: rgba(63,185,80,0.12); }}
            .conf-medium {{ color: #d29922; background: rgba(210,153,34,0.12); }}
            .conf-low {{ color: #f85149; background: rgba(248,81,73,0.12); }}
            
            .correction-select {{
                padding: 8px 12px;
                border: 1px solid #30363d;
                border-radius: 6px;
                font-size: 13px;
                cursor: pointer;
                min-width: 140px;
                background: #0d1117;
                color: #e1e4e8;
            }}
            .correction-select:hover {{ border-color: #667eea; }}
            .correction-select.corrected {{ 
                border-color: #3fb950; 
                background: rgba(63,185,80,0.1);
            }}
            
            .save-btn {{
                background: linear-gradient(135deg, #667eea, #764ba2);
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 6px;
                cursor: pointer;
                font-size: 13px;
                font-weight: 600;
                transition: all 0.2s ease;
            }}
            .save-btn:hover {{ opacity: 0.9; transform: translateY(-1px); }}
            .save-btn:disabled {{ 
                background: #30363d; 
                color: #484f58;
                cursor: not-allowed;
                transform: none;
            }}
            
            .correct-btn {{
                background: linear-gradient(135deg, #238636, #2ea043);
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 6px;
                cursor: pointer;
                font-size: 13px;
                font-weight: 600;
                margin-left: 5px;
                transition: all 0.2s ease;
            }}
            .correct-btn:hover {{ opacity: 0.9; }}
            
            .saved-badge {{
                display: inline-block;
                background: #238636;
                color: white;
                padding: 4px 10px;
                border-radius: 4px;
                font-size: 11px;
                font-weight: 600;
            }}
            
            .toast {{
                position: fixed;
                bottom: 20px;
                right: 20px;
                background: #1c2129;
                color: white;
                padding: 15px 25px;
                border-radius: 10px;
                box-shadow: 0 8px 32px rgba(0,0,0,0.5);
                display: none;
                z-index: 1000;
                border: 1px solid #30363d;
            }}
            .toast.success {{ background: #238636; border-color: #2ea043; }}
            .toast.error {{ background: #da3633; border-color: #f85149; }}
            
            .feedback-info {{
                background: #161b22;
                padding: 15px 20px;
                border-radius: 8px;
                margin-bottom: 20px;
                display: flex;
                justify-content: space-between;
                align-items: center;
                border: 1px solid #30363d;
            }}
            .feedback-info.pending {{
                border-color: #d29922;
                background: rgba(210,153,34,0.06);
            }}
            .feedback-info.ready {{
                border-color: #3fb950;
                background: rgba(63,185,80,0.06);
            }}
            .card {{
                background: #161b22;
                border: 1px solid #30363d;
                border-radius: 12px;
                padding: 20px;
                margin-bottom: 16px;
            }}
            
            /* === BOT CONTROLS PANEL === */
            .controls-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
                gap: 20px;
                margin-top: 20px;
            }}
            .control-card {{
                background: #161b22;
                border: 1px solid #30363d;
                border-radius: 14px;
                padding: 24px;
                transition: border-color 0.3s ease, box-shadow 0.3s ease;
            }}
            .control-card:hover {{
                border-color: rgba(102,126,234,0.3);
                box-shadow: 0 4px 20px rgba(102,126,234,0.08);
            }}
            .control-card h3 {{
                margin: 0 0 16px 0;
                font-size: 18px;
                color: #e1e4e8;
                display: flex;
                align-items: center;
                gap: 10px;
            }}
            .control-card h3 .card-icon {{
                font-size: 22px;
            }}
            .control-card .card-desc {{
                font-size: 13px;
                color: #8b949e;
                margin-bottom: 16px;
                line-height: 1.5;
            }}
            .action-btn {{
                display: inline-flex;
                align-items: center;
                gap: 8px;
                padding: 10px 20px;
                border: none;
                border-radius: 8px;
                cursor: pointer;
                font-size: 14px;
                font-weight: 600;
                transition: all 0.25s ease;
                color: white;
            }}
            .action-btn.primary {{
                background: linear-gradient(135deg, #667eea, #764ba2);
            }}
            .action-btn.success {{
                background: linear-gradient(135deg, #238636, #2ea043);
            }}
            .action-btn.warning {{
                background: linear-gradient(135deg, #9e6a03, #d29922);
            }}
            .action-btn:hover {{
                transform: translateY(-2px);
                box-shadow: 0 4px 16px rgba(0,0,0,0.3);
            }}
            .action-btn:disabled {{
                opacity: 0.5;
                cursor: not-allowed;
                transform: none;
                box-shadow: none;
            }}
            .action-btn .spinner {{
                display: none;
                width: 14px;
                height: 14px;
                border: 2px solid rgba(255,255,255,0.3);
                border-top-color: white;
                border-radius: 50%;
                animation: spin 0.8s linear infinite;
            }}
            .action-btn.loading .spinner {{ display: inline-block; }}
            .action-btn.loading .btn-text {{ opacity: 0.7; }}
            @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
            
            .data-table {{
                width: 100%;
                border-collapse: collapse;
                margin-top: 12px;
            }}
            .data-table th {{
                background: #1c2129;
                padding: 10px 14px;
                text-align: left;
                font-size: 12px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
                color: #8b949e;
                border-bottom: 1px solid #30363d;
            }}
            .data-table td {{
                padding: 10px 14px;
                border-bottom: 1px solid #21262d;
                font-size: 13px;
                color: #c9d1d9;
            }}
            .data-table tr:hover {{ background: rgba(255,255,255,0.03); }}
            .tag-badge {{
                display: inline-block;
                padding: 3px 10px;
                border-radius: 12px;
                font-size: 11px;
                font-weight: 700;
                text-transform: uppercase;
                letter-spacing: 0.5px;
                background: rgba(102,126,234,0.15);
                color: #a8b8ff;
            }}
            .empty-state {{
                text-align: center;
                padding: 40px 20px;
                color: #484f58;
            }}
            .empty-state .empty-icon {{ font-size: 40px; margin-bottom: 12px; }}
            .empty-state p {{ margin: 0; font-size: 14px; }}
            .result-msg {{
                margin-top: 12px;
                padding: 10px 14px;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 500;
                display: none;
            }}
            .result-msg.success {{
                background: rgba(63,185,80,0.1);
                color: #3fb950;
                border: 1px solid rgba(63,185,80,0.2);
                display: block;
            }}
            .result-msg.error {{
                background: rgba(248,81,73,0.1);
                color: #f85149;
                border: 1px solid rgba(248,81,73,0.2);
                display: block;
            }}
            .order-item {{
                background: #0d1117;
                border: 1px solid #21262d;
                border-radius: 10px;
                padding: 14px;
                margin-bottom: 10px;
                transition: border-color 0.2s;
            }}
            .order-item:hover {{ border-color: #30363d; }}
            .order-item .order-header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 8px;
            }}
            .order-item .order-name {{
                font-weight: 600;
                color: #e1e4e8;
            }}
            .order-item .order-time {{
                font-size: 12px;
                color: #8b949e;
            }}
            .order-item .order-msg {{
                font-family: 'JetBrains Mono', monospace;
                font-size: 12px;
                color: #8b949e;
                background: #161b22;
                padding: 8px;
                border-radius: 6px;
                word-break: break-word;
            }}
            .counter-badge {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                min-width: 28px;
                height: 28px;
                border-radius: 14px;
                font-size: 13px;
                font-weight: 700;
                background: rgba(102,126,234,0.2);
                color: #a8b8ff;
                padding: 0 8px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🤖 S4all Bot — Admin Dashboard</h1>
                <div class="stats-bar">
                    <div class="stat-box">
                        <div class="stat-value">{stats['total_classifications']}</div>
                        <div class="stat-label">Classificazioni Totali</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-value">{stats['fallback_rate']*100:.1f}%</div>
                        <div class="stat-label">Fallback Rate</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-value">{feedback_stats['pending']}</div>
                        <div class="stat-label">Feedback Pending</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-value">{feedback_stats['used']}</div>
                        <div class="stat-label">Feedback Usati</div>
                    </div>
                </div>
                <div class="tab-nav">
                    <button class="tab-btn active" onclick="switchTab('ml')" id="tab-ml">🧠 ML Training</button>
                    <button class="tab-btn" onclick="switchTab('controls')" id="tab-controls">🎛️ Comandi Bot</button>
                </div>
            </div>
            
            <!-- TAB: ML Training -->
            <div class="tab-panel active" id="panel-ml">
            
            <!-- Info Modello -->
            <div class="card" style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; margin-bottom: 20px;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <h3 style="margin: 0 0 5px 0;">🤖 Modello ML Attuale</h3>
                        <p style="margin: 0; opacity: 0.9;">{model_info}</p>
                    </div>
                    <div style="text-align: right;">
                        <div style="font-size: 24px; font-weight: bold;">{backup_count}</div>
                        <div style="font-size: 12px; opacity: 0.8;">Backup disponibili</div>
                    </div>
                </div>
            </div>
            
            <div class="feedback-info {'ready' if retraining_ready else 'pending'}">
                <div>
                    <strong>🔄 Retraining Automatico:</strong>
                    {retraining_text}
                    <br><small style="opacity: 0.7;">Controllato ogni 1 ora automaticamente</small>
                </div>
                <div>
                    <span style="font-size: 12px; opacity: 0.8; margin-right: 15px;">
                        Min: 10 | Attuali: {feedback_stats['pending']}
                    </span>
                    {retraining_btn}
                </div>
            </div>
            
            <div class="filters">
                <label>🔍 Filtra per intent:</label>
                <select id="intentFilter" onchange="filterTable()">
                    <option value="">Tutti</option>
                    {intent_options}
                </select>
                
                <label>📊 Confidence:</label>
                <select id="confFilter" onchange="filterTable()">
                    <option value="">Tutte</option>
                    <option value="high">Alta (≥0.85)</option>
                    <option value="medium">Media (0.70-0.85)</option>
                    <option value="low">Bassa (<0.70)</option>
                </select>
                
                <label>🔎 Cerca:</label>
                <input type="text" id="searchFilter" placeholder="Cerca nel testo..." onkeyup="filterTable()">
            </div>
            
            <div class="messages-table">
                <table id="messagesTable">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Messaggio</th>
                            <th>Intent Predetto</th>
                            <th>Confidence</th>
                            <th>Correggi a...</th>
                            <th>Azione</th>
                        </tr>
                    </thead>
                    <tbody>
    {rows_html}
                    </tbody>
                </table>
            </div>
            
            <div class="card" style="margin-top: 20px;">
                <h3 style="color: #e1e4e8;">💾 Modello ML</h3>
                <p>
                    <a href="/admin/download-model?token={auth_token}" class="save-btn" style="text-decoration: none; display: inline-block;">
                        📥 Scarica Modello Aggiornato (.pkl)
                    </a>
                </p>
                <small style="color: #666;">
                    Scarica il file <code>intent_classifier_model.pkl</code> per backup locale o test.
                </small>
            </div>
            </div> <!-- fine panel-ml -->
            
            <!-- TAB: Bot Controls -->
            <div class="tab-panel" id="panel-controls">
                <div class="controls-grid">
                    
                    <!-- CARD: Tags Clienti -->
                    <div class="control-card">
                        <h3><span class="card-icon">📋</span> Tag Clienti <span class="counter-badge" id="tags-count">—</span></h3>
                        <p class="card-desc">Visualizza tutti i clienti registrati con i loro tag di sconto. Equivalente al comando <code>/listtags</code> su Telegram.</p>
                        <button class="action-btn primary" onclick="loadTags()" id="btn-load-tags">
                            <span class="spinner"></span>
                            <span class="btn-text">📥 Carica Tags</span>
                        </button>
                        <div id="tags-container" style="margin-top: 16px;"></div>
                    </div>
                    
                    <!-- CARD: Ordini Oggi -->
                    <div class="control-card">
                        <h3><span class="card-icon">📦</span> Ordini Oggi <span class="counter-badge" id="ordini-count">—</span></h3>
                        <p class="card-desc">Visualizza tutti gli ordini confermati oggi dai clienti. Equivalente al comando <code>/ordini</code> su Telegram.</p>
                        <button class="action-btn primary" onclick="loadOrdini()" id="btn-load-ordini">
                            <span class="spinner"></span>
                            <span class="btn-text">📥 Carica Ordini</span>
                        </button>
                        <div id="ordini-container" style="margin-top: 16px;"></div>
                    </div>
                    
                    <!-- CARD: Aggiorna FAQ -->
                    <div class="control-card">
                        <h3><span class="card-icon">📝</span> Aggiorna FAQ</h3>
                        <p class="card-desc">Scarica e aggiorna le FAQ dal link JustPaste.it configurato. Equivalente al comando <code>/aggiorna_faq</code> su Telegram.</p>
                        <button class="action-btn success" onclick="aggiornaFaq()" id="btn-faq">
                            <span class="spinner"></span>
                            <span class="btn-text">🔄 Aggiorna FAQ</span>
                        </button>
                        <div class="result-msg" id="faq-result"></div>
                    </div>
                    
                    <!-- CARD: Aggiorna Listino -->
                    <div class="control-card">
                        <h3><span class="card-icon">📄</span> Aggiorna Listino</h3>
                        <p class="card-desc">Scarica e aggiorna il listino prodotti dal link JustPaste.it. Aggiorna anche le keywords del classificatore ML. Equivalente a <code>/aggiorna_lista</code>.</p>
                        <button class="action-btn warning" onclick="aggiornaLista()" id="btn-lista">
                            <span class="spinner"></span>
                            <span class="btn-text">🔄 Aggiorna Listino</span>
                        </button>
                        <div class="result-msg" id="lista-result"></div>
                    </div>
                    
                </div>
            </div> <!-- fine panel-controls -->
        </div>
        
        <div class="toast" id="toast"></div>
        
        <script>
            function enableSave(id) {{
                const select = document.getElementById('select-' + id);
                const btn = document.getElementById('btn-' + id);
                btn.disabled = select.value === '';
                if (select.value !== '') {{
                    select.classList.add('corrected');
                }} else {{
                    select.classList.remove('corrected');
                }}
            }}
            
            // Estrai token dall'URL
            const urlParams = new URLSearchParams(window.location.search);
            const authToken = urlParams.get('token');
            
            async function saveCorrection(id) {{
                const row = document.getElementById('row-' + id);
                const text = row.getAttribute('data-full-text');
                const predictedIntent = row.getAttribute('data-intent');
                
                const select = document.getElementById('select-' + id);
                const correctIntent = select.value;
                const btn = document.getElementById('btn-' + id);
                
                if (!correctIntent) return;
                
                btn.disabled = true;
                btn.textContent = 'Salvataggio...';
                
                try {{
                    const response = await fetch('/admin/api/correct?token=' + authToken, {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{
                            id: id,
                            text: text,
                            predicted_intent: predictedIntent,
                            correct_intent: correctIntent
                        }})
                    }});
                    
                    const result = await response.json();
                    
                    if (result.success) {{
                        showToast('✅ Correzione salvata!', 'success');
                        btn.outerHTML = '<span class="saved-badge">✓ Salvato</span>';
                        select.disabled = true;
                        // Nascondi pulsante Corretta
                        const correctBtn = document.getElementById('correct-btn-' + id);
                        if (correctBtn) correctBtn.style.display = 'none';
                        // Aggiorna contatore feedback
                        updateFeedbackCounter(1);
                    }} else {{
                        showToast('❌ Errore: ' + result.message, 'error');
                        btn.disabled = false;
                        btn.textContent = 'Salva';
                    }}
                }} catch (e) {{
                    showToast('❌ Errore di rete', 'error');
                    btn.disabled = false;
                    btn.textContent = 'Salva';
                }}
            }}
            
            async function markCorrect(id) {{
                const row = document.getElementById('row-' + id);
                const text = row.getAttribute('data-full-text');
                const predictedIntent = row.getAttribute('data-intent');
                const btn = document.getElementById('correct-btn-' + id);
                
                btn.disabled = true;
                btn.textContent = 'Salvataggio...';
                
                try {{
                    const response = await fetch('/admin/api/correct?token=' + authToken, {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{
                            id: id,
                            text: text,
                            predicted_intent: predictedIntent,
                            correct_intent: predictedIntent,
                            is_correct: true
                        }})
                    }});
                    
                    const result = await response.json();
                    
                    if (result.success) {{
                        showToast('✅ Segnato come corretto!', 'success');
                        btn.outerHTML = '<span class="saved-badge">✓ Corretta</span>';
                        const select = document.getElementById('select-' + id);
                        if (select) select.disabled = true;
                        const saveBtn = document.getElementById('btn-' + id);
                        if (saveBtn) saveBtn.style.display = 'none';
                    }} else {{
                        showToast('❌ Errore: ' + result.message, 'error');
                        btn.disabled = false;
                        btn.textContent = '✓ Corretta';
                    }}
                }} catch (e) {{
                    showToast('❌ Errore di rete', 'error');
                    btn.disabled = false;
                    btn.textContent = '✓ Corretta';
                }}
            }}
            
            function showToast(message, type) {{
                const toast = document.getElementById('toast');
                toast.textContent = message;
                toast.className = 'toast ' + type;
                toast.style.display = 'block';
                setTimeout(() => {{ toast.style.display = 'none'; }}, 3000);
            }}
            
            function updateFeedbackCounter(increment) {{
                // Trova il contatore pending (il terzo stat-box con label 'Feedback Pending')
                const statBoxes = document.querySelectorAll('.stat-box');
                let pendingEl = null;
                statBoxes.forEach(box => {{
                    const label = box.querySelector('.stat-label');
                    if (label && label.textContent.includes('Feedback Pending')) {{
                        pendingEl = box.querySelector('.stat-value');
                    }}
                }});
                
                let newCount = 0;
                if (pendingEl) {{
                    const current = parseInt(pendingEl.textContent) || 0;
                    newCount = current + increment;
                    pendingEl.textContent = newCount;
                }}
                
                // Aggiorna anche il testo "Min: 10 | Attuali: X" nel div di destra
                const statsBar = document.querySelector('.feedback-info > div:last-child > span');
                if (statsBar) {{
                    statsBar.textContent = 'Min: 10 | Attuali: ' + newCount;
                }}
                
                // Aggiorna testo e stile retraining
                const retrainInfo = document.querySelector('.feedback-info > div:first-child');
                const feedbackInfo = document.querySelector('.feedback-info');
                if (retrainInfo) {{
                    if (newCount >= 10) {{
                        retrainInfo.innerHTML = '<strong>🔄 Retraining Automatico:</strong> Pronto per retraining!<br><small style="opacity: 0.7;">Controllato ogni 1 ora automaticamente</small>';
                        feedbackInfo.classList.remove('pending');
                        feedbackInfo.classList.add('ready');
                        // Aggiungi bottone se non c'è
                        if (!document.querySelector('.feedback-info button')) {{
                            const btnDiv = document.querySelector('.feedback-info > div:last-child');
                            if (btnDiv) {{
                                btnDiv.insertAdjacentHTML('afterbegin', '<button class="save-btn" onclick="forceRetrain()" style="margin-right: 10px;">🚀 Forza Retraining</button>');
                            }}
                        }}
                    }} else {{
                        retrainInfo.innerHTML = '<strong>🔄 Retraining Automatico:</strong> Servono altri ' + (10 - newCount) + ' feedback per il retraining automatico<br><small style="opacity: 0.7;">Controllato ogni 1 ora automaticamente</small>';
                        feedbackInfo.classList.remove('ready');
                        feedbackInfo.classList.add('pending');
                    }}
                }}
            }}
            
            function filterTable() {{
                const intentFilter = document.getElementById('intentFilter').value;
                const confFilter = document.getElementById('confFilter').value;
                const searchFilter = document.getElementById('searchFilter').value.toLowerCase();
                
                const rows = document.querySelectorAll('#messagesTable tbody tr');
                
                rows.forEach(row => {{
                    const intent = row.getAttribute('data-intent');
                    const confidence = parseFloat(row.getAttribute('data-confidence'));
                    const text = row.querySelector('.message-text').textContent.toLowerCase();
                    
                    let show = true;
                    
                    if (intentFilter && intent !== intentFilter) show = false;
                    
                    if (confFilter) {{
                        if (confFilter === 'high' && confidence < 0.85) show = false;
                        if (confFilter === 'medium' && (confidence < 0.70 || confidence >= 0.85)) show = false;
                        if (confFilter === 'low' && confidence >= 0.70) show = false;
                    }}
                    
                    if (searchFilter && !text.includes(searchFilter)) show = false;
                    
                    row.style.display = show ? '' : 'none';
                }});
            }}
            
            // === TAB SWITCHING ===
            function switchTab(tab) {{
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
                document.getElementById('tab-' + tab).classList.add('active');
                document.getElementById('panel-' + tab).classList.add('active');
            }}
            
            // === BOT CONTROLS FUNCTIONS ===
            async function loadTags() {{
                const btn = document.getElementById('btn-load-tags');
                btn.classList.add('loading');
                btn.disabled = true;
                
                try {{
                    const res = await fetch('/admin/api/tags?token=' + authToken);
                    const data = await res.json();
                    
                    if (data.success) {{
                        document.getElementById('tags-count').textContent = data.count;
                        const container = document.getElementById('tags-container');
                        
                        if (data.count === 0) {{
                            container.innerHTML = '<div class="empty-state"><div class="empty-icon">📭</div><p>Nessun cliente registrato con tag</p></div>';
                        }} else {{
                            let html = '<table class="data-table"><thead><tr><th>User ID</th><th>Tag</th></tr></thead><tbody>';
                            data.tags.forEach(t => {{
                                html += `<tr><td><code>${{t.user_id}}</code></td><td><span class="tag-badge">${{t.tag}}</span></td></tr>`;
                            }});
                            html += '</tbody></table>';
                            container.innerHTML = html;
                        }}
                        showToast(`✅ ${{data.count}} tag caricati`, 'success');
                    }} else {{
                        showToast('❌ ' + data.message, 'error');
                    }}
                }} catch (e) {{
                    showToast('❌ Errore di rete', 'error');
                }} finally {{
                    btn.classList.remove('loading');
                    btn.disabled = false;
                }}
            }}
            
            async function loadOrdini() {{
                const btn = document.getElementById('btn-load-ordini');
                btn.classList.add('loading');
                btn.disabled = true;
                
                try {{
                    const res = await fetch('/admin/api/ordini?token=' + authToken);
                    const data = await res.json();
                    
                    if (data.success) {{
                        document.getElementById('ordini-count').textContent = data.count;
                        const container = document.getElementById('ordini-container');
                        
                        if (data.count === 0) {{
                            container.innerHTML = '<div class="empty-state"><div class="empty-icon">📋</div><p>Nessun ordine confermato oggi</p></div>';
                        }} else {{
                            let html = '';
                            data.ordini.forEach((o, i) => {{
                                const name = o.user_name || 'N/A';
                                const user = o.username ? `@${{o.username}}` : '';
                                const msg = o.message ? o.message.substring(0, 150) : 'N/A';
                                const time = o.ora || '';
                                html += `<div class="order-item">
                                    <div class="order-header">
                                        <span class="order-name">${{i+1}}. ${{name}} ${{user}}</span>
                                        <span class="order-time">🕐 ${{time}}</span>
                                    </div>
                                    <div class="order-msg">${{msg}}</div>
                                </div>`;
                            }});
                            container.innerHTML = html;
                        }}
                        showToast(`✅ ${{data.count}} ordini caricati`, 'success');
                    }} else {{
                        showToast('❌ ' + data.message, 'error');
                    }}
                }} catch (e) {{
                    showToast('❌ Errore di rete', 'error');
                }} finally {{
                    btn.classList.remove('loading');
                    btn.disabled = false;
                }}
            }}
            
            async function aggiornaFaq() {{
                const btn = document.getElementById('btn-faq');
                const result = document.getElementById('faq-result');
                btn.classList.add('loading');
                btn.disabled = true;
                result.className = 'result-msg';
                result.style.display = 'none';
                
                try {{
                    const res = await fetch('/admin/api/aggiorna-faq?token=' + authToken, {{ method: 'POST' }});
                    const data = await res.json();
                    
                    if (data.success) {{
                        result.className = 'result-msg success';
                        result.textContent = '✅ ' + data.message;
                        result.style.display = 'block';
                        showToast('✅ FAQ aggiornate!', 'success');
                    }} else {{
                        result.className = 'result-msg error';
                        result.textContent = '❌ ' + data.message;
                        result.style.display = 'block';
                        showToast('❌ Errore FAQ', 'error');
                    }}
                }} catch (e) {{
                    result.className = 'result-msg error';
                    result.textContent = '❌ Errore di rete';
                    result.style.display = 'block';
                    showToast('❌ Errore di rete', 'error');
                }} finally {{
                    btn.classList.remove('loading');
                    btn.disabled = false;
                }}
            }}
            
            async function aggiornaLista() {{
                const btn = document.getElementById('btn-lista');
                const result = document.getElementById('lista-result');
                btn.classList.add('loading');
                btn.disabled = true;
                result.className = 'result-msg';
                result.style.display = 'none';
                
                try {{
                    const res = await fetch('/admin/api/aggiorna-lista?token=' + authToken, {{ method: 'POST' }});
                    const data = await res.json();
                    
                    if (data.success) {{
                        result.className = 'result-msg success';
                        result.textContent = '✅ ' + data.message;
                        result.style.display = 'block';
                        showToast('✅ Listino aggiornato!', 'success');
                    }} else {{
                        result.className = 'result-msg error';
                        result.textContent = '❌ ' + data.message;
                        result.style.display = 'block';
                        showToast('❌ Errore listino', 'error');
                    }}
                }} catch (e) {{
                    result.className = 'result-msg error';
                    result.textContent = '❌ Errore di rete';
                    result.style.display = 'block';
                    showToast('❌ Errore di rete', 'error');
                }} finally {{
                    btn.classList.remove('loading');
                    btn.disabled = false;
                }}
            }}
            
            async function forceRetrain() {{
                if (!confirm('🚀 Vuoi forzare il retraining ora?\\n\\nQuesto può richiedere alcuni secondi.')) return;
                
                showToast('🔄 Avvio retraining...', 'success');
                
                try {{
                    const response = await fetch('/admin/api/retrain?token=' + authToken, {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}}
                    }});
                    
                    const result = await response.json();
                    
                    if (result.success) {{
                        showToast(`✅ Retraining completato! Accuracy: ${{(result.accuracy * 100).toFixed(1)}}%`, 'success');
                        setTimeout(() => location.reload(), 2000);
                    }} else {{
                        showToast('❌ ' + result.message, 'error');
                    }}
                }} catch (e) {{
                    showToast('❌ Errore di rete', 'error');
                }}
            }}
        </script>
    </body>
    </html>
    """
    return html


def _render_intent_detail_html(auth_token, intent_name, distribution, cases):
    """Renderizza l'HTML della pagina dettaglio intent."""
    
    cases_html = ""
    for case in cases:
        conf = case['confidence']
        conf_class = 'high' if conf >= 0.85 else 'medium' if conf >= 0.70 else 'low' if conf >= 0.50 else 'very-low'
        cases_html += f"""
                <tr class="{conf_class}">
                    <td>{html_lib.escape(case['text'])}</td>
                    <td><strong>{conf:.2f}</strong></td>
                    <td>{case['timestamp'][:19]}</td>
                </tr>
        """
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Intent: {intent_name}</title>
        <style>
            body {{ font-family: Arial; margin: 20px; background: #f5f5f5; }}
            .card {{ background: white; padding: 20px; margin: 10px 0; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            .metric {{ display: inline-block; margin: 10px 20px; }}
            .metric-value {{ font-size: 32px; font-weight: bold; color: #2196F3; }}
            .metric-label {{ font-size: 14px; color: #666; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }}
            th {{ background: #2196F3; color: white; }}
            .very-low {{ background: #ffebee; color: #c62828; }}
            .low {{ background: #fff3e0; color: #e65100; }}
            .medium {{ background: #fff9c4; color: #f57f17; }}
            .high {{ background: #e8f5e9; color: #2e7d32; }}
            .back-btn {{ display: inline-block; padding: 10px 20px; background: #2196F3; color: white; text-decoration: none; border-radius: 5px; margin-bottom: 20px; }}
        </style>
    </head>
    <body>
        <a href="/admin/stats?token={auth_token}" class="back-btn">← Torna al Dashboard</a>
        
        <h1>📊 Intent: <code>{intent_name}</code></h1>
        
        <div class="card">
            <h2>📈 Distribuzione Confidence</h2>
            <div class="metric">
                <div class="metric-value">{distribution['total']}</div>
                <div class="metric-label">Totale Casi</div>
            </div>
            <div class="metric">
                <div class="metric-value">{distribution['avg_confidence']:.2f}</div>
                <div class="metric-label">Media Confidence</div>
            </div>
            <div class="metric">
                <div class="metric-value">{distribution['min_confidence']:.2f}</div>
                <div class="metric-label">Min</div>
            </div>
            <div class="metric">
                <div class="metric-value">{distribution['max_confidence']:.2f}</div>
                <div class="metric-label">Max</div>
            </div>
        </div>
        
        <div class="card">
            <h2>📊 Breakdown per Livello</h2>
            <table>
                <tr>
                    <th>Livello</th>
                    <th>Range Confidence</th>
                    <th>Conteggio</th>
                    <th>Percentuale</th>
                </tr>
                <tr class="high">
                    <td><strong>Alta</strong></td>
                    <td>≥ 0.85</td>
                    <td>{distribution['high']}</td>
                    <td>{(distribution['high']/distribution['total']*100):.1f}%</td>
                </tr>
                <tr class="medium">
                    <td><strong>Media</strong></td>
                    <td>0.70 - 0.85</td>
                    <td>{distribution['medium']}</td>
                    <td>{(distribution['medium']/distribution['total']*100):.1f}%</td>
                </tr>
                <tr class="low">
                    <td><strong>Bassa</strong></td>
                    <td>0.50 - 0.70</td>
                    <td>{distribution['low']}</td>
                    <td>{(distribution['low']/distribution['total']*100):.1f}%</td>
                </tr>
                <tr class="very-low">
                    <td><strong>Molto Bassa</strong></td>
                    <td>&lt; 0.50</td>
                    <td>{distribution['very_low']}</td>
                    <td>{(distribution['very_low']/distribution['total']*100):.1f}%</td>
                </tr>
            </table>
        </div>
        
        <div class="card">
            <h2>💬 Tutti i Messaggi ({len(cases)} totali)</h2>
            <table>
                <tr>
                    <th>Messaggio</th>
                    <th>Confidence</th>
                    <th>Timestamp</th>
                </tr>
    {cases_html}
            </table>
        </div>
        
        <div class="card">
            <h3>📥 Export Dati</h3>
            <p><a href="/admin/export_intent/{intent_name}?token={auth_token}">📥 Download JSON per questo intent</a></p>
        </div>
    </body>
    </html>
    """
    return html


def _render_trends_html(auth_token, months, trends):
    """Renderizza l'HTML della pagina trend storici."""
    
    # Dati per il grafico
    labels = []
    fallback_rates = []
    totals = []
    rows_html = ""
    
    for trend in reversed(trends):  # Ordine cronologico per grafico
        labels.append(trend['year_month'])
        fallback_rates.append(float(trend['fallback_rate']))
        totals.append(trend['total'])
        
        # Top 3 intent per questo mese
        by_intent = trend['by_intent']
        top_intents = sorted(by_intent.items(), key=lambda x: x[1]['count'], reverse=True)[:3]
        top_intents_str = ", ".join([f"{intent} ({data['count']})" for intent, data in top_intents])
        
        rows_html += f"""
                <tr>
                    <td><strong>{trend['year_month']}</strong></td>
                    <td>{trend['total']}</td>
                    <td>{trend['fallback_count']}</td>
                    <td>{trend['fallback_rate']}%</td>
                    <td>{top_intents_str}</td>
                </tr>
        """
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Trend Storici</title>
        <style>
            body {{ font-family: Arial; margin: 20px; background: #f5f5f5; }}
            .card {{ background: white; padding: 20px; margin: 10px 0; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            .back-btn {{ display: inline-block; padding: 10px 20px; background: #2196F3; color: white; text-decoration: none; border-radius: 5px; margin-bottom: 20px; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }}
            th {{ background: #2196F3; color: white; }}
            .chart {{ margin: 20px 0; }}
        </style>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    </head>
    <body>
        <a href="/admin/stats?token={auth_token}" class="back-btn">← Torna al Dashboard</a>
        
        <h1>📈 Trend Storici - Ultimi {months} Mesi</h1>
        
        <div class="card">
            <canvas id="trendChart" width="400" height="150"></canvas>
        </div>
        
        <div class="card">
            <h2>📊 Dettaglio Mensile</h2>
            <table>
                <tr>
                    <th>Mese</th>
                    <th>Totale</th>
                    <th>Fallback</th>
                    <th>Fallback Rate</th>
                    <th>Top 3 Intent</th>
                </tr>
    {rows_html}
            </table>
        </div>
        
        <script>
            const ctx = document.getElementById('trendChart').getContext('2d');
            const chart = new Chart(ctx, {{
                type: 'line',
                data: {{
                    labels: {json.dumps(labels)},
                    datasets: [
                        {{
                            label: 'Fallback Rate (%)',
                            data: {json.dumps(fallback_rates)},
                            borderColor: 'rgb(255, 99, 132)',
                            backgroundColor: 'rgba(255, 99, 132, 0.1)',
                            yAxisID: 'y'
                        }},
                        {{
                            label: 'Totale Classificazioni',
                            data: {json.dumps(totals)},
                            borderColor: 'rgb(54, 162, 235)',
                            backgroundColor: 'rgba(54, 162, 235, 0.1)',
                            yAxisID: 'y1'
                        }}
                    ]
                }},
                options: {{
                    responsive: true,
                    interaction: {{
                        mode: 'index',
                        intersect: false
                    }},
                    scales: {{
                        y: {{
                            type: 'linear',
                            display: true,
                            position: 'left',
                            title: {{
                                display: true,
                                text: 'Fallback Rate (%)'
                            }}
                        }},
                        y1: {{
                            type: 'linear',
                            display: true,
                            position: 'right',
                            title: {{
                                display: true,
                                text: 'Totale'
                            }},
                            grid: {{
                                drawOnChartArea: false
                            }}
                        }}
                    }}
                }}
            }});
        </script>
    </body>
    </html>
    """
    return html
