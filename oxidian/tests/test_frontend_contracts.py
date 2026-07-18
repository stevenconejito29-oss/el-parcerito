import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendContractsTest(unittest.TestCase):
    def test_templates_do_not_use_inline_dom_event_attributes(self):
        """La CSP de producción bloquea onclick/onsubmit aunque el HTML pinte bien."""
        pattern = re.compile(
            r"<[^>]+\s(?:onclick|onchange|oninput|onsubmit|onerror|onload)\s*=",
            re.IGNORECASE,
        )
        offenders = []
        for path in (ROOT / "templates").rglob("*.html"):
            if pattern.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_shared_admin_interactions_load_for_every_internal_role(self):
        template = (ROOT / "templates" / "admin_base.html").read_text(encoding="utf-8")

        self.assertEqual(template.count("js/admin-interactions.js"), 1)
        self.assertIn(
            "{% endif %}\n<script nonce=\"{{ csp_nonce() }}\" "
            "src=\"{{ url_for('static', filename='js/admin-interactions.js', v=asset_version) }}\" defer></script>",
            template,
        )

    def test_pos_product_data_is_not_embedded_in_inline_javascript(self):
        template = (ROOT / "templates" / "pos" / "venta.html").read_text(encoding="utf-8")

        self.assertNotIn('onclick="seleccionarProducto(', template)
        for attribute in (
            "data-product-id",
            "data-product-name",
            "data-product-price",
            "data-product-stock",
            "data-product-combo",
        ):
            self.assertIn(attribute, template)
        self.assertIn("button.dataset.productName", template)
        self.assertNotIn("const vacio = document.getElementById('ticket-vacio')", template)
        self.assertIn('container.innerHTML = `<div id="ticket-vacio"', template)

    def test_pos_has_one_primary_clear_and_history_action(self):
        template = (ROOT / "templates" / "pos" / "venta.html").read_text(encoding="utf-8")

        self.assertEqual(template.count("url_for('pos.historial')"), 1)
        self.assertEqual(template.count('data-pos-action="new-sale"'), 1)
        self.assertIn("if (action === 'new-sale') limpiarTicket()", template)
        self.assertNotIn('onclick="limpiarTicket()"', template)
        self.assertNotIn("pos-link-hist", template)
        self.assertNotIn("pos-btn-clear", template)

    def test_pos_uses_mobile_viewport_and_touch_safe_controls(self):
        template = (ROOT / "templates" / "pos" / "venta.html").read_text(encoding="utf-8")
        admin_base = (ROOT / "templates" / "admin_base.html").read_text(encoding="utf-8")

        self.assertIn("var(--app-height, 100dvh)", template)
        self.assertNotIn("height: calc(100vh - 80px)", template)
        self.assertIn("@media(max-width:1099px)", template)
        self.assertIn(".pos-item-btn{width:44px;height:44px}", template)
        self.assertIn("font-size:16px", template)
        self.assertNotIn('class="ox-admin-bnav"', admin_base)
        self.assertIn("role-single-nav", admin_base)

    def test_internal_roles_have_one_navigation_architecture(self):
        template = (ROOT / "templates" / "admin_base.html").read_text(encoding="utf-8")
        shell = (ROOT / "static" / "css" / "role-shell.css").read_text(encoding="utf-8")

        self.assertNotIn('class="ox-admin-bnav"', template)
        self.assertNotIn(".ox-admin-bnav", shell)
        self.assertIn("filename='css/tokens.css'", template)
        self.assertIn("@media (max-width: 1099px)", shell)
        self.assertIn("@media (min-width: 1100px)", shell)
        self.assertNotIn('onclick="sidebarOpen()"', template)
        self.assertNotIn('onclick="sidebarClose()"', template)
        self.assertIn("data-sidebar-open", template)
        self.assertIn("data-sidebar-close", template)
        self.assertIn("addEventListener('click', sidebarOpen)", template)

    def test_delivery_does_not_repeat_commissions_in_main_content(self):
        template = (ROOT / "templates" / "repartidor" / "ruta.html").read_text(encoding="utf-8")

        self.assertNotIn("url_for('repartidor.mis_comisiones')", template)
        self.assertIn("data-delivery-theme-toggle", template)

    def test_public_navigation_does_not_prefetch_session_documents(self):
        script = (ROOT / "static" / "js" / "header-modern.js").read_text(encoding="utf-8")
        template = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")

        self.assertNotIn("link.rel = 'prefetch'", script)
        self.assertNotIn("var criticalRoutes = ['/', '/carrito']", script)
        self.assertNotIn("keepalive: true, mode: 'no-cors'", script)
        self.assertNotIn('type="speculationrules"', template)

    def test_tablet_uses_header_actions_without_floating_obstructions(self):
        template = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        styles = (ROOT / "static" / "css" / "header-modern.css").read_text(encoding="utf-8")

        self.assertIn('class="ox-header-info"', template)
        self.assertNotIn('class="ox-whatsapp-fab"', template)
        self.assertIn("@media (min-width: 761px)", styles)
        self.assertIn(".ox-bottom-nav.ox-bnav-v2 { display: none !important; }", styles)

    def test_portrait_phone_does_not_repeat_cart_navigation(self):
        styles = (ROOT / "static" / "css" / "header-modern.css").read_text(encoding="utf-8")

        self.assertIn("@media (max-width: 760px) and (orientation: portrait)", styles)
        self.assertIn(".ox-header-cart { display: none !important; }", styles)
        self.assertIn("@media (orientation: landscape) and (max-height: 500px)", styles)

    def test_mobile_menu_and_search_are_distinct_navigation_modes(self):
        template = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "js" / "spa-nav.js").read_text(encoding="utf-8")

        self.assertIn("data-bnav=\"home\"", template)
        self.assertIn("data-bnav=\"search\"", template)
        self.assertIn("current.hash === '#buscar'", script)
        self.assertIn("a.dataset.bnav === 'search'", script)
        self.assertIn("activateSearchTarget(url)", script)
        self.assertIn("window.addEventListener('hashchange'", script)
        self.assertIn("if (targetRoute === renderedRoute)", script)
        self.assertIn("function canSwapDocument(doc, targetUrl)", script)
        self.assertIn("stylesheetContract(document, currentUrl)", script)
        self.assertIn("scriptContract(document, currentUrl)", script)
        self.assertIn("if (!canSwapDocument(doc, url))", script)
        self.assertIn("nonce=\"{{ csp_nonce() }}\" src=\"{{ url_for('static', filename='js/spa-nav.js'", template)
        self.assertNotIn("function prefetch(url)", script)

        styles = (ROOT / "static" / "css" / "header-modern.css").read_text(encoding="utf-8")
        self.assertIn("color: var(--navigation-active-text) !important", styles)
        self.assertNotIn("color: var(--brand-on-primary, #1B0A00) !important", styles)

    def test_view_transition_names_are_unique_for_both_cart_links(self):
        styles = (ROOT / "static" / "css" / "header-modern.css").read_text(encoding="utf-8")

        self.assertIn(".ox-header-cart { view-transition-name: ox-header-cart; }", styles)
        self.assertIn(".ox-bnav-cart { view-transition-name: ox-bottom-cart; }", styles)
        self.assertNotIn("view-transition-name: ox-cart-btn", styles)

    def test_mobile_footer_is_compact_and_keeps_legal_information_once(self):
        template = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        styles = (ROOT / "static" / "css" / "oxidian.css").read_text(encoding="utf-8")

        self.assertIn('class="ep-footer-mobile"', template)
        self.assertEqual(template.count("Privacidad y condiciones"), 1)
        self.assertIn(".ep-footer-inner { display: none; }", styles)
        self.assertIn("env(safe-area-inset-bottom)", styles)

    def test_staff_push_prompt_can_be_dismissed_for_the_navigation_session(self):
        template = (ROOT / "templates" / "admin_base.html").read_text(encoding="utf-8")
        pwa_manager = (ROOT / "static" / "js" / "pwa-manager.js").read_text(encoding="utf-8")

        self.assertIn("data-push-dismiss", template)
        self.assertIn("sessionStorage.getItem('ox.pushPromptDismissed')", pwa_manager)
        self.assertIn("sessionStorage.setItem('ox.pushPromptDismissed', '1')", pwa_manager)
        self.assertNotIn("this.closest('#ox-push-banner').style.display='none'", template)

    def test_public_actions_are_separate_from_brand_identity(self):
        base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        tokens = (ROOT / "static" / "css" / "tokens.css").read_text(encoding="utf-8")
        header = (ROOT / "static" / "css" / "header-modern.css").read_text(encoding="utf-8")

        self.assertIn("--brand-on-accent: {{ brand.on_acento }}", base)
        self.assertIn("--action-primary:", tokens)
        self.assertIn("background: var(--action-primary) !important", header)
        self.assertNotIn("Ítem activo — pill dorada", header)

    def test_public_motion_and_elevation_use_shared_tokens(self):
        tokens = (ROOT / "static" / "css" / "tokens.css").read_text(encoding="utf-8")
        menu = (ROOT / "static" / "css" / "storefront-menu.css").read_text(encoding="utf-8")
        header = (ROOT / "static" / "css" / "header-modern.css").read_text(encoding="utf-8")

        for token in ("--motion-fast:", "--motion-base:", "--motion-slow:", "--elevation-raised:"):
            self.assertIn(token, tokens)
        self.assertIn("var(--motion-base)", menu)
        self.assertIn("var(--elevation-raised)", menu)
        self.assertIn("var(--motion-slow)", header)
        self.assertNotIn("@keyframes cardRise", menu)

    def test_product_addition_has_accessible_visual_feedback(self):
        card = (ROOT / "templates" / "public" / "_product_card.html").read_text(encoding="utf-8")
        menu = (ROOT / "static" / "css" / "storefront-menu.css").read_text(encoding="utf-8")
        template = (ROOT / "templates" / "public" / "index.html").read_text(encoding="utf-8")

        self.assertIn('data-product-card="{{ p.id }}"', card)
        self.assertIn("form.setAttribute('aria-busy', 'true')", template)
        self.assertIn("form.removeAttribute('aria-busy')", template)
        self.assertIn("epConfirmProductAdded(_modalProductId)", template)
        self.assertIn(".ep-card.is-just-added", menu)
        self.assertIn("@keyframes ep-product-confirm", menu)

    def test_storefront_categories_cycle_configurable_palette_tokens(self):
        template = (ROOT / "templates" / "public" / "index.html").read_text(encoding="utf-8")
        styles = (ROOT / "static" / "css" / "storefront-menu.css").read_text(encoding="utf-8")

        self.assertIn('data-palette="{{ loop.index0 % 5 }}"', template)
        self.assertIn('data-palette="promo"', template)
        self.assertIn('.ep-cat-section[data-palette="2"] .ep-card', styles)
        self.assertIn("var(--visual-promo)", styles)
        self.assertIn("var(--visual-highlight)", styles)

    def test_public_notices_do_not_float_over_content(self):
        base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        styles = (ROOT / "static" / "css" / "oxidian.css").read_text(encoding="utf-8")

        self.assertNotIn('class="ox-store-notice"', base)
        self.assertNotIn("ox-push-prompt--floating", base)
        self.assertNotIn(".ox-push-prompt--floating", styles)
        self.assertIn("body.ox-body-public > .ox-pwa-sheet", styles)
        self.assertIn("position: static", styles)

    def test_points_redemption_has_one_interactive_flow(self):
        cart = (ROOT / "templates" / "public" / "carrito.html").read_text(encoding="utf-8")
        checkout = (ROOT / "templates" / "public" / "checkout.html").read_text(encoding="utf-8")

        self.assertNotIn("crSolicitarCodigo", cart)
        self.assertNotIn("crToggleCanje", cart)
        self.assertNotIn("cr-discounts", cart)
        self.assertIn("requestRewardCode", checkout)
        self.assertIn("verifyRewardCode", checkout)
        self.assertIn("chooseReward", checkout)
        self.assertNotIn("descuentoPuntos", checkout)
        self.assertNotIn("nif_invitado", checkout)
        self.assertIn("Canjear una recompensa", checkout)

    def test_public_header_keeps_configured_color_while_scrolling(self):
        styles = (ROOT / "static" / "css" / "header-modern.css").read_text(encoding="utf-8")
        script = (ROOT / "static" / "js" / "header-modern.js").read_text(encoding="utf-8")

        self.assertIn("background: var(--hdr-ink) !important", styles)
        self.assertIn("backdrop-filter: none !important", styles)
        self.assertNotIn("background: rgba(10,6,5,.95)", styles)
        self.assertNotIn("is-scrolling", script)

    def test_toasts_respect_ios_dynamic_island_safe_area(self):
        styles = (ROOT / "static" / "css" / "oxidian.css").read_text(encoding="utf-8")

        self.assertIn("top: max(1rem, calc(env(safe-area-inset-top, 0px) + .75rem))", styles)
        self.assertIn("right: max(1rem, env(safe-area-inset-right, 0px))", styles)
        self.assertIn("left: max(1rem, env(safe-area-inset-left, 0px))", styles)

    def test_public_controls_do_not_use_csp_blocked_inline_handlers(self):
        templates = [ROOT / "templates" / "base.html", *(ROOT / "templates" / "public").glob("*.html")]
        inline_handler = re.compile(r"\son(?:click|change|input|error|submit|load)\s*=", re.IGNORECASE)

        for template_path in templates:
            template = template_path.read_text(encoding="utf-8")
            self.assertIsNone(inline_handler.search(template), template_path.name)

    def test_mobile_navigation_uses_one_configurable_contrast_pair(self):
        tokens = (ROOT / "static" / "css" / "tokens.css").read_text(encoding="utf-8")
        styles = (ROOT / "static" / "css" / "header-modern.css").read_text(encoding="utf-8")
        base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")

        self.assertIn("--navigation-surface:", tokens)
        self.assertIn("--navigation-text:", tokens)
        self.assertIn("color: var(--navigation-text) !important", styles)
        self.assertIn("var(--navigation-surface) 100%", styles)
        self.assertNotIn("body.ox-body-public .ox-bottom-nav {", base)
        self.assertIn("body.ox-body-public.ox-modal-open .ox-bottom-nav.ox-bnav-v2", styles)
        self.assertIn("visibility: hidden !important", styles)

    def test_colombian_identity_copy_is_configurable_and_responsive(self):
        menu = (ROOT / "templates" / "public" / "index.html").read_text(encoding="utf-8")
        cart = (ROOT / "templates" / "public" / "carrito.html").read_text(encoding="utf-8")
        menu_css = (ROOT / "static" / "css" / "storefront-menu.css").read_text(encoding="utf-8")
        cart_css = (ROOT / "static" / "css" / "storefront-cart.css").read_text(encoding="utf-8")
        heritage_css = (ROOT / "static" / "css" / "heritage.css").read_text(encoding="utf-8")
        pattern = (ROOT / "static" / "colombia-pattern.svg").read_text(encoding="utf-8")

        self.assertIn("ui.menu_catalog_title", menu)
        self.assertIn("ep-hero-cordillera", menu)
        self.assertNotIn("ep-memory-ribbon", menu)
        self.assertIn("ui.cart_memory_note", cart)
        self.assertIn("cr-colombia-emblem", cart)
        self.assertNotIn(".ep-memory-ribbon", menu_css)
        self.assertIn("--heritage-sun", menu_css)
        self.assertIn(".ep-card::before", menu_css)
        self.assertIn(".ep-catalog-mark", menu_css)
        self.assertIn("grid-template-columns: auto minmax(0, 1fr)", menu_css)
        self.assertIn(".cr-memory-note", cart_css)
        self.assertIn("overflow-wrap: anywhere", cart_css)
        self.assertIn('mask-image: url("../colombia-pattern.svg")', heritage_css)
        self.assertIn("body.ox-body-public main::before", heritage_css)
        self.assertIn("viewBox=\"0 0 320 240\"", pattern)

    def test_heritage_design_system_reuses_svg_icons_and_configurable_loyalty_copy(self):
        base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        sprite = (ROOT / "templates" / "partials" / "heritage_sprite.html").read_text(encoding="utf-8")
        heritage = (ROOT / "static" / "css" / "heritage.css").read_text(encoding="utf-8")
        club = (ROOT / "templates" / "public" / "club.html").read_text(encoding="utf-8")
        checkout = (ROOT / "templates" / "public" / "checkout.html").read_text(encoding="utf-8")
        points = (ROOT / "templates" / "public" / "puntos_consulta.html").read_text(encoding="utf-8")

        self.assertIn("css/heritage.css", base)
        self.assertIn("heritage_sprite.html", base)
        for symbol in ("grano", "canasto", "mariposa", "casita"):
            self.assertIn(f'id="ox-hi-{symbol}"', sprite)
        self.assertNotIn(".ep-hero-origin", heritage)
        self.assertIn(".ox-modal__heritage", heritage)
        self.assertIn(".ox-toast-v2::after", heritage)
        self.assertIn(".checkout-memory", heritage)
        self.assertIn(".order-success-hero", heritage)
        self.assertNotIn("sombrero", (ROOT / "templates" / "public" / "index.html").read_text(encoding="utf-8"))
        for template in (club, checkout, points):
            self.assertIn("ui.loyalty_unit_plural", template)
            self.assertNotIn("⭐", template)
            self.assertNotIn("cafecito", template.lower())

        cart = (ROOT / "templates" / "public" / "carrito.html").read_text(encoding="utf-8")
        menu = (ROOT / "templates" / "public" / "index.html").read_text(encoding="utf-8")
        self.assertIn("ui.cart_name", cart)
        self.assertIn("ui.cart_add_action", menu)
        self.assertNotIn("Añadir al carrito", menu)

    def test_cart_state_is_synchronized_from_the_server_response(self):
        base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        menu = (ROOT / "templates" / "public" / "index.html").read_text(encoding="utf-8")
        cart_ui = (ROOT / "static" / "js" / "cart-ui.js").read_text(encoding="utf-8")
        styles = (ROOT / "static" / "css" / "header-modern.css").read_text(encoding="utf-8")

        self.assertIn("js/cart-ui.js", base)
        self.assertIn("window.OxCartUI.setCount(data.cart_count", menu)
        self.assertIn("headerCart?.classList.toggle('has-items'", cart_ui)
        self.assertIn("bottomCart?.classList.toggle('has-items'", cart_ui)
        self.assertIn("navigator.setAppBadge(count)", cart_ui)
        self.assertIn(".ox-bnav-cart.has-items", styles)
        self.assertIn("background: var(--status-err) !important", styles)

    def test_admin_orders_are_paginated_without_per_card_workload_queries(self):
        route = (ROOT / "routes" / "admin.py").read_text(encoding="utf-8")
        template = (ROOT / "templates" / "admin" / "pedidos.html").read_text(encoding="utf-8")

        self.assertIn('SiteConfig.get("ADMIN_PEDIDOS_PAGE_SIZE", "30")', route)
        self.assertIn(".paginate(", route)
        self.assertIn("carga_actual_preparadores", route)
        self.assertIn("carga_actual_repartidores", route)
        self.assertNotIn("pedidos_activos_como_preparador()", template)
        self.assertNotIn("pedidos_activos_como_repartidor()", template)
        self.assertIn('class="ord-pagination"', template)


if __name__ == "__main__":
    unittest.main()
