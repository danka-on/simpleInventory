"""Web hooks for Sweet Shelves."""

import re as _re
from flask import request
from . import inventory_history as ss_inventory_history, runtime as ss_runtime


def inject_server_info():
    """Inject server information into all templates"""
    hostname = request.headers.get('Host', '')
    is_testing = 'nexuscentralhq.org' in hostname and not hostname.startswith('pi.')
    return dict(is_testing_server=is_testing, server_hostname=hostname)


_I18N_SRC_PAT = _re.compile(r'src=(["\'])/static/i18n\.js(?:\?[^"\']*)?\1')


_I18N_CACHE_V = int(getattr(ss_runtime.app, 'start_time', 0) or 0)


def add_no_cache_headers(response):
    try:
        content_type = response.content_type or ''

        if 'text/html' in content_type:
            try:
                if (not getattr(response, 'direct_passthrough', False)) and (not getattr(response, 'is_streamed', False)):
                    html = response.get_data(as_text=True)
                    if html:
                        needs_write = False
                        v = _I18N_CACHE_V

                        # Only run regex if i18n.js is already in the HTML
                        if 'i18n.js' in html:
                            if v:
                                new_html = _I18N_SRC_PAT.sub(lambda m: f'src={m.group(1)}/static/i18n.js?v={v}{m.group(1)}', html)
                            else:
                                new_html = _I18N_SRC_PAT.sub(r'src=\1/static/i18n.js\1', html)
                            if new_html is not html:
                                html = new_html
                                needs_write = True
                        else:
                            # Inject i18n.js for legacy templates that don't include it
                            inject = f'\n<script src="/static/i18n.js?v={v}"></script>\n' if v else '\n<script src="/static/i18n.js"></script>\n'
                            if '</body>' in html:
                                html = html.replace('</body>', inject + '</body>', 1)
                            elif '</html>' in html:
                                html = html.replace('</html>', inject + '</html>', 1)
                            else:
                                html = html + inject
                            needs_write = True

                        if needs_write:
                            response.set_data(html)
            except Exception:
                pass

        if 'text/html' in content_type or 'application/json' in content_type:
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
    except Exception:
        pass
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        try:
            # Give route-specific history breadcrumbs time to commit before reconciliation.
            ss_inventory_history._flush_searchrack_history_outbox(limit=25, settle_seconds=2)
        except Exception:
            # The durable outbox remains in searchRack.db and the background worker retries it.
            pass
    return response
