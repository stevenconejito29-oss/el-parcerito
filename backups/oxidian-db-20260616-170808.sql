--
-- PostgreSQL database dump
--

\restrict peGD4cWdTsoJAysd4U2scI3yDVkhkzj7zuidAcmoPO3crezGpb1eJaj2sP7Btxi

-- Dumped from database version 15.17 (Debian 15.17-1.pgdg13+1)
-- Dumped by pg_dump version 15.17 (Debian 15.17-1.pgdg13+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: admin_features; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.admin_features (
    id integer NOT NULL,
    user_id integer NOT NULL,
    feature character varying(50) NOT NULL,
    activo boolean,
    actualizado_por integer,
    actualizado_en timestamp without time zone
);


ALTER TABLE public.admin_features OWNER TO oxidian;

--
-- Name: admin_features_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.admin_features_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.admin_features_id_seq OWNER TO oxidian;

--
-- Name: admin_features_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.admin_features_id_seq OWNED BY public.admin_features.id;


--
-- Name: affiliate_codes; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.affiliate_codes (
    id integer NOT NULL,
    codigo character varying(30) NOT NULL,
    descripcion character varying(200),
    tipo character varying(20),
    user_id integer,
    descuento_tipo character varying(20),
    descuento_valor numeric(10,2),
    comision_tipo character varying(20),
    comision_valor numeric(10,2),
    activo boolean,
    usos_maximos integer,
    usos_actuales integer,
    fecha_inicio date,
    fecha_fin date,
    creado_en timestamp without time zone,
    creado_por integer
);


ALTER TABLE public.affiliate_codes OWNER TO oxidian;

--
-- Name: affiliate_codes_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.affiliate_codes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.affiliate_codes_id_seq OWNER TO oxidian;

--
-- Name: affiliate_codes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.affiliate_codes_id_seq OWNED BY public.affiliate_codes.id;


--
-- Name: affiliate_uses; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.affiliate_uses (
    id integer NOT NULL,
    codigo_id integer NOT NULL,
    pedido_id integer NOT NULL,
    cliente_id integer NOT NULL,
    descuento_aplicado numeric(10,2),
    comision_generada numeric(10,2),
    comision_pagada boolean,
    creado_en timestamp without time zone,
    staff_payment_id integer
);


ALTER TABLE public.affiliate_uses OWNER TO oxidian;

--
-- Name: affiliate_uses_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.affiliate_uses_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.affiliate_uses_id_seq OWNER TO oxidian;

--
-- Name: affiliate_uses_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.affiliate_uses_id_seq OWNED BY public.affiliate_uses.id;


--
-- Name: audit_log; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.audit_log (
    id integer NOT NULL,
    user_id integer,
    accion character varying(100) NOT NULL,
    recurso character varying(50),
    recurso_id integer,
    detalle text,
    ip character varying(50),
    creado_en timestamp without time zone
);


ALTER TABLE public.audit_log OWNER TO oxidian;

--
-- Name: audit_log_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.audit_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.audit_log_id_seq OWNER TO oxidian;

--
-- Name: audit_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.audit_log_id_seq OWNED BY public.audit_log.id;


--
-- Name: caja; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.caja (
    id integer NOT NULL,
    tipo character varying(20) NOT NULL,
    categoria character varying(30),
    monto numeric(10,2) NOT NULL,
    concepto character varying(200),
    pedido_id integer,
    staff_payment_id integer,
    registrado_por integer,
    fecha timestamp without time zone
);


ALTER TABLE public.caja OWNER TO oxidian;

--
-- Name: caja_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.caja_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.caja_id_seq OWNER TO oxidian;

--
-- Name: caja_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.caja_id_seq OWNED BY public.caja.id;


--
-- Name: campanas_marketing; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.campanas_marketing (
    id integer NOT NULL,
    titulo character varying(200) NOT NULL,
    mensaje text NOT NULL,
    filtro_audiencia character varying(50),
    zona_id integer,
    enviados integer,
    estado character varying(20),
    error_detalle text,
    creado_por integer NOT NULL,
    creado_en timestamp without time zone,
    enviado_en timestamp without time zone
);


ALTER TABLE public.campanas_marketing OWNER TO oxidian;

--
-- Name: campanas_marketing_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.campanas_marketing_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.campanas_marketing_id_seq OWNER TO oxidian;

--
-- Name: campanas_marketing_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.campanas_marketing_id_seq OWNED BY public.campanas_marketing.id;


--
-- Name: categorias; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.categorias (
    id integer NOT NULL,
    nombre character varying(80) NOT NULL,
    descripcion text,
    imagen_url text,
    activo boolean,
    orden integer
);


ALTER TABLE public.categorias OWNER TO oxidian;

--
-- Name: categorias_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.categorias_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.categorias_id_seq OWNER TO oxidian;

--
-- Name: categorias_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.categorias_id_seq OWNED BY public.categorias.id;


--
-- Name: combo_groups; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.combo_groups (
    id integer NOT NULL,
    combo_id integer NOT NULL,
    nombre character varying(80) NOT NULL,
    tipo character varying(20) NOT NULL,
    min_selecciones integer NOT NULL,
    max_selecciones integer NOT NULL,
    orden integer NOT NULL,
    requerido boolean,
    descripcion text,
    creado_en timestamp without time zone
);


ALTER TABLE public.combo_groups OWNER TO oxidian;

--
-- Name: combo_groups_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.combo_groups_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.combo_groups_id_seq OWNER TO oxidian;

--
-- Name: combo_groups_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.combo_groups_id_seq OWNED BY public.combo_groups.id;


--
-- Name: combo_items; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.combo_items (
    id integer NOT NULL,
    combo_id integer NOT NULL,
    producto_id integer NOT NULL,
    cantidad integer NOT NULL,
    es_seleccionable boolean,
    grupo_seleccion character varying(50),
    max_selecciones integer,
    combo_group_id integer,
    orden integer DEFAULT 0 NOT NULL,
    precio_extra numeric(10,2) DEFAULT 0 NOT NULL,
    es_predeterminado boolean DEFAULT false,
    activo boolean DEFAULT true,
    notas_preparacion text
);


ALTER TABLE public.combo_items OWNER TO oxidian;

--
-- Name: combo_items_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.combo_items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.combo_items_id_seq OWNER TO oxidian;

--
-- Name: combo_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.combo_items_id_seq OWNED BY public.combo_items.id;


--
-- Name: coupons; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.coupons (
    id integer NOT NULL,
    codigo character varying(30) NOT NULL,
    descripcion character varying(200),
    tipo character varying(20) NOT NULL,
    valor numeric(10,2) NOT NULL,
    minimo_pedido numeric(10,2),
    usos_maximos integer,
    usos_actuales integer,
    activo boolean,
    fecha_inicio date,
    fecha_fin date
);


ALTER TABLE public.coupons OWNER TO oxidian;

--
-- Name: coupons_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.coupons_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.coupons_id_seq OWNER TO oxidian;

--
-- Name: coupons_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.coupons_id_seq OWNED BY public.coupons.id;


--
-- Name: menu_config; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.menu_config (
    id integer NOT NULL,
    tipo character varying(30) NOT NULL,
    titulo character varying(200),
    contenido text,
    imagen_url text,
    enlace_url text,
    orden integer,
    activo boolean,
    pagina character varying(30),
    categoria_id integer,
    producto_id integer,
    creado_por integer,
    actualizado_en timestamp without time zone
);


ALTER TABLE public.menu_config OWNER TO oxidian;

--
-- Name: menu_config_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.menu_config_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.menu_config_id_seq OWNER TO oxidian;

--
-- Name: menu_config_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.menu_config_id_seq OWNED BY public.menu_config.id;


--
-- Name: notification_outbox; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.notification_outbox (
    id integer NOT NULL,
    canal character varying(30) NOT NULL,
    evento character varying(60) NOT NULL,
    destinatario character varying(200) NOT NULL,
    payload_json text NOT NULL,
    estado character varying(20) NOT NULL,
    intentos integer NOT NULL,
    max_intentos integer NOT NULL,
    siguiente_intento_en timestamp without time zone,
    ultimo_error text,
    pedido_id integer,
    user_id integer,
    creado_en timestamp without time zone NOT NULL,
    enviado_en timestamp without time zone
);


ALTER TABLE public.notification_outbox OWNER TO oxidian;

--
-- Name: notification_outbox_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.notification_outbox_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.notification_outbox_id_seq OWNER TO oxidian;

--
-- Name: notification_outbox_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.notification_outbox_id_seq OWNED BY public.notification_outbox.id;


--
-- Name: order_events; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.order_events (
    id integer NOT NULL,
    pedido_id integer NOT NULL,
    tipo character varying(50) NOT NULL,
    estado_anterior character varying(30),
    estado_nuevo character varying(30),
    actor_id integer,
    canal character varying(30),
    detalle text,
    metadata_json text,
    creado_en timestamp without time zone NOT NULL
);


ALTER TABLE public.order_events OWNER TO oxidian;

--
-- Name: order_events_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.order_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.order_events_id_seq OWNER TO oxidian;

--
-- Name: order_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.order_events_id_seq OWNED BY public.order_events.id;


--
-- Name: order_items; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.order_items (
    id integer NOT NULL,
    pedido_id integer NOT NULL,
    producto_id integer NOT NULL,
    cantidad integer NOT NULL,
    precio_unit numeric(10,2) NOT NULL,
    subtotal numeric(10,2) NOT NULL,
    notas text,
    metadata_json text
);


ALTER TABLE public.order_items OWNER TO oxidian;

--
-- Name: order_items_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.order_items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.order_items_id_seq OWNER TO oxidian;

--
-- Name: order_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.order_items_id_seq OWNED BY public.order_items.id;


--
-- Name: order_provider_status; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.order_provider_status (
    id integer NOT NULL,
    pedido_id integer NOT NULL,
    proveedor_id integer NOT NULL,
    preparado boolean NOT NULL,
    preparado_en timestamp without time zone,
    actualizado_por integer
);


ALTER TABLE public.order_provider_status OWNER TO oxidian;

--
-- Name: order_provider_status_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.order_provider_status_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.order_provider_status_id_seq OWNER TO oxidian;

--
-- Name: order_provider_status_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.order_provider_status_id_seq OWNED BY public.order_provider_status.id;


--
-- Name: orders; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.orders (
    id integer NOT NULL,
    numero_pedido character varying(20) NOT NULL,
    cliente_id integer NOT NULL,
    estado character varying(30) NOT NULL,
    origen character varying(20),
    subtotal numeric(10,2) NOT NULL,
    descuento numeric(10,2),
    total numeric(10,2) NOT NULL,
    cupon_id integer,
    puntos_usados integer,
    puntos_ganados integer,
    metodo_pago character varying(30),
    direccion_entrega text,
    notas text,
    preparador_id integer,
    repartidor_id integer,
    cajero_id integer,
    creado_en timestamp without time zone,
    entregado_en timestamp without time zone,
    zona_id integer,
    afiliado_codigo_id integer,
    es_entrega_epicentro boolean,
    codigo_confirmacion character varying(8),
    codigo_confirmado_en timestamp without time zone,
    intentos_codigo integer,
    pago_confirmado boolean,
    pago_confirmado_por integer,
    pago_confirmado_en timestamp without time zone,
    whatsapp_enviado_confirmacion boolean,
    resena_calificacion integer,
    resena_comentario text,
    resena_enviada boolean,
    proveedor_preparado boolean DEFAULT false NOT NULL,
    proveedor_preparado_en timestamp without time zone
);


ALTER TABLE public.orders OWNER TO oxidian;

--
-- Name: orders_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.orders_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.orders_id_seq OWNER TO oxidian;

--
-- Name: orders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.orders_id_seq OWNED BY public.orders.id;


--
-- Name: points_log; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.points_log (
    id integer NOT NULL,
    cliente_id integer NOT NULL,
    pedido_id integer,
    tipo character varying(20) NOT NULL,
    cantidad integer NOT NULL,
    descripcion character varying(200),
    creado_en timestamp without time zone
);


ALTER TABLE public.points_log OWNER TO oxidian;

--
-- Name: points_log_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.points_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.points_log_id_seq OWNER TO oxidian;

--
-- Name: points_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.points_log_id_seq OWNED BY public.points_log.id;


--
-- Name: price_history; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.price_history (
    id integer NOT NULL,
    producto_id integer NOT NULL,
    precio_anterior numeric(10,2),
    precio_nuevo numeric(10,2) NOT NULL,
    cambiado_por integer,
    cambiado_en timestamp without time zone,
    motivo character varying(200)
);


ALTER TABLE public.price_history OWNER TO oxidian;

--
-- Name: price_history_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.price_history_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.price_history_id_seq OWNER TO oxidian;

--
-- Name: price_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.price_history_id_seq OWNED BY public.price_history.id;


--
-- Name: products; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.products (
    id integer NOT NULL,
    nombre character varying(150) NOT NULL,
    descripcion text,
    precio numeric(10,2) NOT NULL,
    precio_costo numeric(10,2),
    categoria_id integer,
    imagen_url text,
    origen_pais character varying(50),
    es_combo boolean,
    tipo_producto character varying(50),
    atributos_json text,
    activo boolean,
    creado_en timestamp without time zone,
    tipo_entrega character varying(20),
    fecha_llegada date,
    dias_anticipacion_encargo integer,
    hora_inicio_visibilidad time without time zone,
    hora_fin_visibilidad time without time zone,
    dias_semana_json text,
    stock_mostrar_en_web boolean,
    canjeable_con_puntos boolean,
    puntos_para_canje integer,
    es_hipoalergenico boolean,
    alergenos_info text,
    alergenos_json text,
    combo_precio_modo character varying(30) DEFAULT 'fijo'::character varying NOT NULL,
    combo_descuento_pct numeric(5,2) DEFAULT 0 NOT NULL,
    combo_precio_base numeric(10,2) DEFAULT 0 NOT NULL,
    canal_preparacion character varying(20) DEFAULT 'cocina'::character varying NOT NULL,
    proveedor_id integer
);


ALTER TABLE public.products OWNER TO oxidian;

--
-- Name: products_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.products_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.products_id_seq OWNER TO oxidian;

--
-- Name: products_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.products_id_seq OWNED BY public.products.id;


--
-- Name: push_subscriptions; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.push_subscriptions (
    id integer NOT NULL,
    user_id integer,
    endpoint text NOT NULL,
    p256dh text NOT NULL,
    auth character varying(100) NOT NULL,
    rol character varying(30),
    user_agent character varying(300),
    creado_en timestamp without time zone,
    ultimo_uso timestamp without time zone,
    activo boolean
);


ALTER TABLE public.push_subscriptions OWNER TO oxidian;

--
-- Name: push_subscriptions_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.push_subscriptions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.push_subscriptions_id_seq OWNER TO oxidian;

--
-- Name: push_subscriptions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.push_subscriptions_id_seq OWNED BY public.push_subscriptions.id;


--
-- Name: reviews; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.reviews (
    id integer NOT NULL,
    producto_id integer NOT NULL,
    cliente_id integer NOT NULL,
    pedido_id integer,
    calificacion integer NOT NULL,
    comentario text,
    aprobada boolean,
    creado_en timestamp without time zone
);


ALTER TABLE public.reviews OWNER TO oxidian;

--
-- Name: reviews_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.reviews_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.reviews_id_seq OWNER TO oxidian;

--
-- Name: reviews_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.reviews_id_seq OWNED BY public.reviews.id;


--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.schema_migrations (
    id character varying(120) NOT NULL,
    description text,
    applied_at timestamp without time zone NOT NULL
);


ALTER TABLE public.schema_migrations OWNER TO oxidian;

--
-- Name: site_config; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.site_config (
    id integer NOT NULL,
    clave character varying(50) NOT NULL,
    valor text,
    descripcion character varying(200),
    actualizado_en timestamp without time zone,
    actualizado_por integer
);


ALTER TABLE public.site_config OWNER TO oxidian;

--
-- Name: site_config_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.site_config_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.site_config_id_seq OWNER TO oxidian;

--
-- Name: site_config_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.site_config_id_seq OWNED BY public.site_config.id;


--
-- Name: staff_payments; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.staff_payments (
    id integer NOT NULL,
    user_id integer NOT NULL,
    tipo character varying(20) NOT NULL,
    monto numeric(10,2) NOT NULL,
    concepto character varying(200),
    periodo_inicio date,
    periodo_fin date,
    pedido_id integer,
    pagado boolean,
    fecha_pago timestamp without time zone,
    registrado_por integer,
    creado_en timestamp without time zone,
    origen character varying(30) DEFAULT 'manual'::character varying NOT NULL
);


ALTER TABLE public.staff_payments OWNER TO oxidian;

--
-- Name: staff_payments_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.staff_payments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.staff_payments_id_seq OWNER TO oxidian;

--
-- Name: staff_payments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.staff_payments_id_seq OWNED BY public.staff_payments.id;


--
-- Name: stock; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.stock (
    id integer NOT NULL,
    producto_id integer NOT NULL,
    cantidad integer NOT NULL,
    unidad character varying(20),
    lote character varying(50),
    fecha_entrada date,
    fecha_caducidad date,
    alerta_dias integer,
    ubicacion character varying(100)
);


ALTER TABLE public.stock OWNER TO oxidian;

--
-- Name: stock_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.stock_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.stock_id_seq OWNER TO oxidian;

--
-- Name: stock_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.stock_id_seq OWNED BY public.stock.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.users (
    id integer NOT NULL,
    nombre character varying(100) NOT NULL,
    email character varying(120) NOT NULL,
    password_hash character varying(256) NOT NULL,
    rol character varying(20) NOT NULL,
    telefono character varying(20),
    direccion text,
    puntos integer,
    activo boolean,
    creado_en timestamp without time zone,
    last_seen timestamp without time zone,
    en_linea boolean,
    cod_puntos character varying(8),
    cod_puntos_expira timestamp without time zone,
    cod_puntos_intentos integer,
    puesto_trabajo character varying(100),
    salario_base numeric(10,2),
    tarifa_entrega numeric(10,2),
    telefono_normalizado character varying(20)
);


ALTER TABLE public.users OWNER TO oxidian;

--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.users_id_seq OWNER TO oxidian;

--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: zonas_entrega; Type: TABLE; Schema: public; Owner: oxidian
--

CREATE TABLE public.zonas_entrega (
    id integer NOT NULL,
    nombre character varying(100) NOT NULL,
    descripcion text,
    es_epicentro boolean,
    activo boolean,
    precio_envio numeric(10,2),
    tiempo_estimado_min integer,
    gratis_desde numeric(10,2),
    orden integer
);


ALTER TABLE public.zonas_entrega OWNER TO oxidian;

--
-- Name: zonas_entrega_id_seq; Type: SEQUENCE; Schema: public; Owner: oxidian
--

CREATE SEQUENCE public.zonas_entrega_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.zonas_entrega_id_seq OWNER TO oxidian;

--
-- Name: zonas_entrega_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: oxidian
--

ALTER SEQUENCE public.zonas_entrega_id_seq OWNED BY public.zonas_entrega.id;


--
-- Name: admin_features id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.admin_features ALTER COLUMN id SET DEFAULT nextval('public.admin_features_id_seq'::regclass);


--
-- Name: affiliate_codes id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.affiliate_codes ALTER COLUMN id SET DEFAULT nextval('public.affiliate_codes_id_seq'::regclass);


--
-- Name: affiliate_uses id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.affiliate_uses ALTER COLUMN id SET DEFAULT nextval('public.affiliate_uses_id_seq'::regclass);


--
-- Name: audit_log id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.audit_log ALTER COLUMN id SET DEFAULT nextval('public.audit_log_id_seq'::regclass);


--
-- Name: caja id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.caja ALTER COLUMN id SET DEFAULT nextval('public.caja_id_seq'::regclass);


--
-- Name: campanas_marketing id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.campanas_marketing ALTER COLUMN id SET DEFAULT nextval('public.campanas_marketing_id_seq'::regclass);


--
-- Name: categorias id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.categorias ALTER COLUMN id SET DEFAULT nextval('public.categorias_id_seq'::regclass);


--
-- Name: combo_groups id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.combo_groups ALTER COLUMN id SET DEFAULT nextval('public.combo_groups_id_seq'::regclass);


--
-- Name: combo_items id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.combo_items ALTER COLUMN id SET DEFAULT nextval('public.combo_items_id_seq'::regclass);


--
-- Name: coupons id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.coupons ALTER COLUMN id SET DEFAULT nextval('public.coupons_id_seq'::regclass);


--
-- Name: menu_config id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.menu_config ALTER COLUMN id SET DEFAULT nextval('public.menu_config_id_seq'::regclass);


--
-- Name: notification_outbox id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.notification_outbox ALTER COLUMN id SET DEFAULT nextval('public.notification_outbox_id_seq'::regclass);


--
-- Name: order_events id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.order_events ALTER COLUMN id SET DEFAULT nextval('public.order_events_id_seq'::regclass);


--
-- Name: order_items id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.order_items ALTER COLUMN id SET DEFAULT nextval('public.order_items_id_seq'::regclass);


--
-- Name: order_provider_status id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.order_provider_status ALTER COLUMN id SET DEFAULT nextval('public.order_provider_status_id_seq'::regclass);


--
-- Name: orders id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.orders ALTER COLUMN id SET DEFAULT nextval('public.orders_id_seq'::regclass);


--
-- Name: points_log id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.points_log ALTER COLUMN id SET DEFAULT nextval('public.points_log_id_seq'::regclass);


--
-- Name: price_history id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.price_history ALTER COLUMN id SET DEFAULT nextval('public.price_history_id_seq'::regclass);


--
-- Name: products id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.products ALTER COLUMN id SET DEFAULT nextval('public.products_id_seq'::regclass);


--
-- Name: push_subscriptions id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.push_subscriptions ALTER COLUMN id SET DEFAULT nextval('public.push_subscriptions_id_seq'::regclass);


--
-- Name: reviews id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.reviews ALTER COLUMN id SET DEFAULT nextval('public.reviews_id_seq'::regclass);


--
-- Name: site_config id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.site_config ALTER COLUMN id SET DEFAULT nextval('public.site_config_id_seq'::regclass);


--
-- Name: staff_payments id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.staff_payments ALTER COLUMN id SET DEFAULT nextval('public.staff_payments_id_seq'::regclass);


--
-- Name: stock id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.stock ALTER COLUMN id SET DEFAULT nextval('public.stock_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: zonas_entrega id; Type: DEFAULT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.zonas_entrega ALTER COLUMN id SET DEFAULT nextval('public.zonas_entrega_id_seq'::regclass);


--
-- Data for Name: admin_features; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.admin_features (id, user_id, feature, activo, actualizado_por, actualizado_en) FROM stdin;
1	1	caja	t	\N	2026-06-02 14:29:50.661201
2	1	productos	t	\N	2026-06-02 14:29:50.663274
3	1	stock	t	\N	2026-06-02 14:29:50.665039
4	1	cupones	t	\N	2026-06-02 14:29:50.666714
5	1	staff_pagos	t	\N	2026-06-02 14:29:50.668386
6	1	reportes	t	\N	2026-06-02 14:29:50.670199
7	1	zonas	t	\N	2026-06-02 14:29:50.671645
8	1	auditoria	t	\N	2026-06-02 14:29:50.672971
9	1	marketing	t	\N	2026-06-02 14:29:50.674219
10	1	pos	t	\N	2026-06-02 14:29:50.675346
11	1	whatsapp	t	\N	2026-06-02 14:29:50.676314
12	51	caja	t	\N	2026-06-11 20:08:42.95982
13	51	productos	t	\N	2026-06-11 20:08:42.984542
14	51	stock	t	\N	2026-06-11 20:08:42.985619
15	51	cupones	t	\N	2026-06-11 20:08:42.986647
16	51	staff_pagos	t	\N	2026-06-11 20:08:42.987695
17	51	reportes	t	\N	2026-06-11 20:08:42.988783
18	51	zonas	t	\N	2026-06-11 20:08:42.989987
19	51	auditoria	t	\N	2026-06-11 20:08:42.991132
20	51	marketing	t	\N	2026-06-11 20:08:42.99219
21	51	pos	t	\N	2026-06-11 20:08:42.993215
22	51	whatsapp	t	\N	2026-06-11 20:08:42.994208
\.


--
-- Data for Name: affiliate_codes; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.affiliate_codes (id, codigo, descripcion, tipo, user_id, descuento_tipo, descuento_valor, comision_tipo, comision_valor, activo, usos_maximos, usos_actuales, fecha_inicio, fecha_fin, creado_en, creado_por) FROM stdin;
\.


--
-- Data for Name: affiliate_uses; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.affiliate_uses (id, codigo_id, pedido_id, cliente_id, descuento_aplicado, comision_generada, comision_pagada, creado_en, staff_payment_id) FROM stdin;
\.


--
-- Data for Name: audit_log; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.audit_log (id, user_id, accion, recurso, recurso_id, detalle, ip, creado_en) FROM stdin;
1	1	config_update	site_config	\N	NOMBRE_NEGOCIO=El Parcerito	192.168.1.41	2026-06-02 14:55:51.603343
2	1	config_update	site_config	\N	APP_ICON_URL=/uploads/icon/app-icon.png	192.168.1.41	2026-06-02 14:56:37.767947
3	1	config_update	site_config	\N	RADIO_ENTREGA_KM=10	192.168.1.41	2026-06-02 14:58:18.816461
4	1	crear_categoria	categoria	4	AREPAS	192.168.1.41	2026-06-02 14:59:56.652405
5	1	crear_categoria	categoria	5	GASEOSAS	192.168.1.41	2026-06-02 15:01:58.640831
6	1	agregar_stock	product	10	+4 uds — lote sin lote	192.168.1.41	2026-06-02 15:29:18.906878
7	1	agregar_stock	product	11	+7 uds — lote sin lote	192.168.1.41	2026-06-02 15:31:40.960672
8	1	agregar_stock	product	12	+50 uds — lote sin lote	192.168.1.41	2026-06-02 15:35:25.424019
9	1	agregar_stock	product	13	+60 uds — lote sin lote	192.168.1.41	2026-06-02 15:35:37.55205
13	1	crear_categoria	categoria	18	combos	172.21.0.1	2026-06-08 11:38:10.754956
18	1	config_update	site_config	\N	RADIO_ENTREGA_KM=15	172.21.0.1	2026-06-09 01:08:01.413569
19	1	editar_zona	zona_entrega	2	\N	172.21.0.1	2026-06-09 01:08:16.063805
25	1	config_section_update	site_config	\N	HORARIO_APERTURA, HORARIO_CIERRE, TIENDA_FORZAR_CERRADA	172.21.0.1	2026-06-09 09:12:22.646943
31	1	crear_usuario	user	40	cocina@oxidian.com [cocina]	192.168.1.41	2026-06-09 15:45:57.176173
34	1	avanzar_pedido	order	39	armando	172.21.0.1	2026-06-11 16:44:45.812048
35	1	asignar_pedido	order	49	\N	172.21.0.1	2026-06-11 16:45:11.974541
36	1	asignar_pedido	order	48	\N	172.21.0.1	2026-06-11 16:45:33.422325
37	1	avanzar_pedido	order	39	listo	172.21.0.1	2026-06-11 16:45:39.38716
38	1	avanzar_pedido	order	49	armando	172.21.0.1	2026-06-11 16:45:42.128011
39	1	avanzar_pedido	order	49	listo	172.21.0.1	2026-06-11 16:45:43.067148
40	1	avanzar_pedido	order	49	en_ruta	172.21.0.1	2026-06-11 16:45:43.858543
41	1	avanzar_pedido	order	39	en_ruta	172.21.0.1	2026-06-11 16:45:47.191616
42	1	avanzar_pedido	order	48	armando	172.21.0.1	2026-06-11 16:47:05.973066
43	1	asignar_pedido	order	48	\N	172.21.0.1	2026-06-11 16:48:03.983374
44	1	avanzar_pedido	order	38	en_ruta	172.21.0.1	2026-06-11 16:48:18.797391
45	1	avanzar_pedido	order	48	listo	172.21.0.1	2026-06-11 16:48:28.061646
46	1	avanzar_pedido	order	49	entregado	172.21.0.1	2026-06-11 16:48:39.324084
47	1	ajustar_stock	stock	2	0→100 uds (producto 11)	192.168.1.41	2026-06-11 22:13:30.526272
48	1	ajustar_stock	stock	1	0→100 uds (producto 10)	192.168.1.41	2026-06-11 22:13:34.954825
\.


--
-- Data for Name: caja; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.caja (id, tipo, categoria, monto, concepto, pedido_id, staff_payment_id, registrado_por, fecha) FROM stdin;
37	ingreso	venta_online	7.00	Pedido OX-20260609-17784111	38	\N	\N	2026-06-09 15:37:25.882213
38	ingreso	venta_online	37.00	Pedido OX-20260609-00843267	39	\N	\N	2026-06-09 19:11:47.359669
45	ingreso	venta_online	6.80	Pedido OX-20260609-42251390	48	\N	\N	2026-06-09 22:06:03.691838
46	ingreso	venta_online	38.10	Pedido OX-20260610-18324098	49	\N	\N	2026-06-10 15:31:17.943062
47	ingreso	venta_online	19.50	Pedido OX-20260611-40636213	50	\N	\N	2026-06-11 20:17:40.981483
48	ingreso	venta_online	17.50	Pedido OX-20260611-00930280	51	\N	\N	2026-06-11 20:19:39.539899
49	ingreso	venta_online	20.55	Pedido OX-20260611-91614470	52	\N	\N	2026-06-11 22:16:30.291264
50	ingreso	venta_online	8.00	Pedido OX-20260611-83072597	53	\N	\N	2026-06-11 22:17:24.91068
\.


--
-- Data for Name: campanas_marketing; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.campanas_marketing (id, titulo, mensaje, filtro_audiencia, zona_id, enviados, estado, error_detalle, creado_por, creado_en, enviado_en) FROM stdin;
\.


--
-- Data for Name: categorias; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.categorias (id, nombre, descripcion, imagen_url, activo, orden) FROM stdin;
4	AREPAS	RICAS AREPAS COLOMBIANAS	https://imgs.search.brave.com/tpyrclbCGWTOaZiycIKWFhoML5ochGRGwiffNEFK2aQ/rs:fit:500:0:1:0/g:ce/aHR0cHM6Ly93d3cu/dG9kb3NhY29tZXIu/bmV0L3dwLWNvbnRl/bnQvdXBsb2Fkcy8y/MDIzLzExL2FyZXBh/cy1xdWVzby1jb2xv/bWJpYW5hcy0xLmpw/Zw	t	0
5	GASEOSAS	BEBIDAS REFRESCANTRES	https://imgs.search.brave.com/_jQHV7HT4TpiAX3GylsvD6hXSOxmwSNGvmHXxyvKifs/rs:fit:860:0:0:0/g:ce/aHR0cHM6Ly9pLnBp/bmltZy5jb20vb3Jp/Z2luYWxzLzE3LzUz/LzA3LzE3NTMwNzgy/ZGEwNmIxN2QxZTY2/NTQ1MjZkYmRlMmJl/LmpwZw	t	0
18	combos	\N	\N	t	0
\.


--
-- Data for Name: combo_groups; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.combo_groups (id, combo_id, nombre, tipo, min_selecciones, max_selecciones, orden, requerido, descripcion, creado_en) FROM stdin;
3	14	Base incluida	fijo	0	1	0	t	\N	2026-06-05 16:42:33.652752
17	14	bebidas	seleccion	1	1	2	t	\N	2026-06-08 11:25:05.258325
\.


--
-- Data for Name: combo_items; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.combo_items (id, combo_id, producto_id, cantidad, es_seleccionable, grupo_seleccion, max_selecciones, combo_group_id, orden, precio_extra, es_predeterminado, activo, notas_preparacion) FROM stdin;
28	14	10	1	f	\N	1	3	0	0.00	f	t	\N
29	14	11	1	f	\N	1	3	1	0.00	f	t	\N
31	14	13	1	t	bebidas	1	17	3	0.00	f	t	\N
32	14	12	1	t	bebidas	1	17	3	0.00	f	t	\N
\.


--
-- Data for Name: coupons; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.coupons (id, codigo, descripcion, tipo, valor, minimo_pedido, usos_maximos, usos_actuales, activo, fecha_inicio, fecha_fin) FROM stdin;
\.


--
-- Data for Name: menu_config; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.menu_config (id, tipo, titulo, contenido, imagen_url, enlace_url, orden, activo, pagina, categoria_id, producto_id, creado_por, actualizado_en) FROM stdin;
26	producto_destacado	ghgghh	lñjñgujklfhjkjhkfjkhjfkghjk	\N	\N	0	f	home	\N	12	1	2026-06-09 20:29:53.28846
\.


--
-- Data for Name: notification_outbox; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.notification_outbox (id, canal, evento, destinatario, payload_json, estado, intentos, max_intentos, siguiente_intento_en, ultimo_error, pedido_id, user_id, creado_en, enviado_en) FROM stdin;
129	whatsapp	points_balance	+34622663874	{"telefono": "+34622663874", "mensaje": "⭐ *Tus puntos en El Parcerito de Carmona*\\n\\nTienes *1044 puntos* disponibles.\\nEquivalen hasta a *€10.44* de descuento.\\n\\nPara canjearlos, arma tu pedido en la web y verifica este mismo WhatsApp durante la confirmación."}	sent	1	3	\N	\N	\N	39	2026-06-09 22:02:06.853443	2026-06-09 22:02:07.534808
130	whatsapp	points_otp	+34622663874	{"telefono": "+34622663874", "mensaje": "🔐 *Código de verificación — El Parcerito de Carmona*\\n\\nTu código para canjear puntos por *1044 puntos* es:\\n\\n*232179*\\n\\n⏰ Válido 10 minutos. No lo compartas."}	sent	1	3	\N	\N	\N	39	2026-06-09 22:02:13.276104	2026-06-09 22:02:13.871504
131	whatsapp	order_state	+34622663874	{"telefono": "+34622663874", "mensaje": "🎉 *¡Pedido recibido!*\\nTu pedido *OX-20260609-42251390* ya está registrado. Total: *€6.80*.\\n¡Estamos en ello ahora mismo! 🔥", "numero_pedido": "OX-20260609-42251390", "estado": "pendiente"}	sent	1	3	\N	\N	48	39	2026-06-09 22:06:03.708949	2026-06-09 22:06:04.303092
132	whatsapp	order_state	+34622663974	{"telefono": "+34622663974", "mensaje": "🎉 *¡Pedido recibido!*\\nTu pedido *OX-20260610-18324098* ya está registrado. Total: *€38.10*.\\n¡Estamos en ello ahora mismo! 🔥", "numero_pedido": "OX-20260610-18324098", "estado": "pendiente"}	failed	3	3	2026-06-10 15:46:09.744225	send_failed	49	49	2026-06-10 15:31:18.003897	\N
133	whatsapp	order_state	+34622663874	{"telefono": "+34622663874", "mensaje": "🔥 *¡Manos a la obra!*\\nTu pedido *OX-20260609-00843267* está en preparación en este momento.\\nEn breve te avisamos cuando esté listo. ✨", "numero_pedido": "OX-20260609-00843267", "estado": "armando"}	sent	1	3	\N	\N	39	39	2026-06-11 16:44:45.83895	2026-06-11 16:44:47.107345
134	whatsapp	order_state	+34622663874	{"telefono": "+34622663874", "mensaje": "✅ *¡Tu pedido está listo!*\\nEl pedido *OX-20260609-00843267* está perfectamente preparado y en breve sale hacia ti. 📦", "numero_pedido": "OX-20260609-00843267", "estado": "listo"}	sent	1	3	\N	\N	39	39	2026-06-11 16:45:39.40414	2026-06-11 16:45:40.515653
138	whatsapp	order_state	+34622663874	{"telefono": "+34622663874", "mensaje": "🚀 *¡En camino!*\\nTu pedido *OX-20260609-00843267* ya va hacia ti.\\n\\n🔐 *Código de confirmación: 804780*\\nEl repartidor te lo pedirá al entregar — tenlo a mano. 🛵", "numero_pedido": "OX-20260609-00843267", "estado": "en_ruta"}	sent	1	3	\N	\N	39	39	2026-06-11 16:45:47.238854	2026-06-11 16:45:48.330698
139	whatsapp	order_state	+34622663874	{"telefono": "+34622663874", "mensaje": "🔥 *¡Manos a la obra!*\\nTu pedido *OX-20260609-42251390* está en preparación en este momento.\\nEn breve te avisamos cuando esté listo. ✨", "numero_pedido": "OX-20260609-42251390", "estado": "armando"}	sent	1	3	\N	\N	48	39	2026-06-11 16:47:05.991985	2026-06-11 16:47:07.080409
109	whatsapp	order_state	+34622663874	{"telefono": "+34622663874", "mensaje": "✅ Tu pedido OX-20260609-17784111 fue recibido. Total: €7.00. ¡Ya lo estamos preparando!", "numero_pedido": "OX-20260609-17784111", "estado": "pendiente"}	sent	1	3	\N	\N	38	39	2026-06-09 15:37:25.919944	2026-06-09 15:37:27.165585
110	whatsapp	order_state	+34622663874	{"telefono": "+34622663874", "mensaje": "👨‍🍳 Estamos armando tu pedido OX-20260609-17784111. En breve saldrá.", "numero_pedido": "OX-20260609-17784111", "estado": "armando"}	sent	1	3	2026-06-09 15:48:39.524889	send_failed	38	39	2026-06-09 15:46:37.913215	2026-06-09 15:46:39.332809
140	whatsapp	order_state	+34622663874	{"telefono": "+34622663874", "mensaje": "🚀 *¡En camino!*\\nTu pedido *OX-20260609-17784111* ya va hacia ti.\\n\\n🔐 *Código de confirmación: 368649*\\nEl repartidor te lo pedirá al entregar — tenlo a mano. 🛵", "numero_pedido": "OX-20260609-17784111", "estado": "en_ruta"}	sent	1	3	\N	\N	38	39	2026-06-11 16:48:18.810648	2026-06-11 16:48:19.984151
111	whatsapp	order_state	+34622663874	{"telefono": "+34622663874", "mensaje": "📦 Tu pedido OX-20260609-17784111 está listo y pronto saldrá a entregarse.", "numero_pedido": "OX-20260609-17784111", "estado": "listo"}	sent	1	3	\N	\N	38	39	2026-06-09 15:46:46.691624	2026-06-09 15:46:47.810207
112	whatsapp	order_state	+34622663874	{"telefono": "+34622663874", "mensaje": "🎉 *¡Pedido recibido!*\\nTu pedido *OX-20260609-00843267* ya está registrado. Total: *€37.00*.\\n¡Estamos en ello ahora mismo! 🔥", "numero_pedido": "OX-20260609-00843267", "estado": "pendiente"}	sent	1	3	\N	\N	39	39	2026-06-09 19:11:47.375832	2026-06-09 19:11:48.308196
141	whatsapp	order_state	+34622663874	{"telefono": "+34622663874", "mensaje": "✅ *¡Tu pedido está listo!*\\nEl pedido *OX-20260609-42251390* está perfectamente preparado y en breve sale hacia ti. 📦", "numero_pedido": "OX-20260609-42251390", "estado": "listo"}	sent	1	3	\N	\N	48	39	2026-06-11 16:48:28.069112	2026-06-11 16:48:29.301676
135	whatsapp	order_state	+34622663974	{"telefono": "+34622663974", "mensaje": "🔥 *¡Manos a la obra!*\\nTu pedido *OX-20260610-18324098* está en preparación en este momento.\\nEn breve te avisamos cuando esté listo. ✨", "numero_pedido": "OX-20260610-18324098", "estado": "armando"}	failed	3	3	2026-06-11 17:00:13.447704	send_failed	49	49	2026-06-11 16:45:42.147346	\N
136	whatsapp	order_state	+34622663974	{"telefono": "+34622663974", "mensaje": "✅ *¡Tu pedido está listo!*\\nEl pedido *OX-20260610-18324098* está perfectamente preparado y en breve sale hacia ti. 📦", "numero_pedido": "OX-20260610-18324098", "estado": "listo"}	failed	3	3	2026-06-11 17:00:14.310157	send_failed	49	49	2026-06-11 16:45:43.090742	\N
137	whatsapp	order_state	+34622663974	{"telefono": "+34622663974", "mensaje": "🚀 *¡En camino!*\\nTu pedido *OX-20260610-18324098* ya va hacia ti.\\n\\n🔐 *Código de confirmación: 025839*\\nEl repartidor te lo pedirá al entregar — tenlo a mano. 🛵", "numero_pedido": "OX-20260610-18324098", "estado": "en_ruta"}	failed	3	3	2026-06-11 17:00:15.156399	send_failed	49	49	2026-06-11 16:45:43.889578	\N
142	whatsapp	order_state	+34622663974	{"telefono": "+34622663974", "mensaje": "🎊 *¡Pedido entregado!*\\n¡Esperamos que te haya encantado! 😍\\nGanaste *38 puntos* 🌟 — van sumando para tu próximo descuento.\\n¡Gracias por elegirnos! 💛", "numero_pedido": "OX-20260610-18324098", "estado": "entregado"}	failed	3	3	2026-06-11 17:03:23.577763	send_failed	49	49	2026-06-11 16:48:39.346342	\N
143	whatsapp	review_request	+34622663974	{"telefono": "+34622663974", "mensaje": "⭐ *¿Cómo estuvo tu pedido OX-20260610-18324098?*\\n\\nResponde con una calificación del 1 al 5 y, si quieres, un comentario.\\nTu opinión nos ayuda a mejorar."}	failed	3	3	2026-06-11 17:04:57.96875	send_failed	49	49	2026-06-11 16:48:39.476704	\N
144	whatsapp	order_state	+34622663874	{"telefono": "+34622663874", "mensaje": "🎉 *¡Pedido recibido!*\\nTu pedido *OX-20260611-40636213* ya está registrado. Total: *€19.50*.\\n¡Estamos en ello ahora mismo! 🔥", "numero_pedido": "OX-20260611-40636213", "estado": "pendiente"}	sent	1	3	\N	\N	50	39	2026-06-11 20:17:41.00063	2026-06-11 20:17:41.030584
145	whatsapp	points_otp	+34622663874	{"telefono": "+34622663874", "mensaje": "🔐 *Código de verificación — Parcerito*\\n\\nTu código para canjear puntos por *1041 puntos* es:\\n\\n*213629*\\n\\n⏰ Válido 10 minutos. No lo compartas."}	sent	1	3	\N	\N	\N	39	2026-06-11 20:17:49.195891	2026-06-11 20:17:49.208382
146	whatsapp	points_otp	+34622663874	{"telefono": "+34622663874", "mensaje": "🔐 *Código de verificación — Parcerito*\\n\\nTu código para canjear puntos por *1041 puntos* es:\\n\\n*889870*\\n\\n⏰ Válido 10 minutos. No lo compartas."}	sent	1	3	\N	\N	\N	39	2026-06-11 20:17:55.290175	2026-06-11 20:17:55.416441
147	whatsapp	points_balance	+34622663874	{"telefono": "+34622663874", "mensaje": "⭐ *Tus puntos en Parcerito*\\n\\nTienes *1041 puntos* disponibles.\\nEquivalen hasta a *€10.41* de descuento.\\n\\nPara canjearlos, arma tu pedido en la web y verifica este mismo WhatsApp durante la confirmación."}	sent	1	3	\N	\N	\N	39	2026-06-11 20:17:55.97507	2026-06-11 20:17:55.990418
149	whatsapp	points_balance	+34622663874	{"telefono": "+34622663874", "mensaje": "⭐ *Tus puntos en Parcerito*\\n\\nTienes *1041 puntos* disponibles.\\nEquivalen hasta a *€10.41* de descuento.\\n\\nPara canjearlos, arma tu pedido en la web y verifica este mismo WhatsApp durante la confirmación."}	sent	1	3	\N	\N	\N	39	2026-06-11 20:18:47.696046	2026-06-11 20:18:47.703536
148	whatsapp	points_otp	+34622663874	{"telefono": "+34622663874", "mensaje": "🔐 *Código de verificación — Parcerito*\\n\\nTu código para canjear puntos por *1041 puntos* es:\\n\\n*425541*\\n\\n⏰ Válido 10 minutos. No lo compartas."}	sent	1	3	\N	\N	\N	39	2026-06-11 20:18:43.957163	2026-06-11 20:18:43.972505
150	whatsapp	points_otp	+34622663874	{"telefono": "+34622663874", "mensaje": "🔐 *Código de verificación — Parcerito*\\n\\nTu código para canjear puntos por *1041 puntos* es:\\n\\n*371986*\\n\\n⏰ Válido 10 minutos. No lo compartas."}	sent	1	3	\N	\N	\N	39	2026-06-11 20:19:28.63478	2026-06-11 20:19:28.644207
151	whatsapp	points_balance	+34622663874	{"telefono": "+34622663874", "mensaje": "⭐ *Tus puntos en Parcerito*\\n\\nTienes *1041 puntos* disponibles.\\nEquivalen hasta a *€10.41* de descuento.\\n\\nPara canjearlos, arma tu pedido en la web y verifica este mismo WhatsApp durante la confirmación."}	sent	1	3	\N	\N	\N	39	2026-06-11 20:19:31.798456	2026-06-11 20:19:31.815955
152	whatsapp	order_state	+34622663874	{"telefono": "+34622663874", "mensaje": "🎉 *¡Pedido recibido!*\\nTu pedido *OX-20260611-00930280* ya está registrado. Total: *€17.50*.\\n¡Estamos en ello ahora mismo! 🔥", "numero_pedido": "OX-20260611-00930280", "estado": "pendiente"}	sent	1	3	\N	\N	51	39	2026-06-11 20:19:39.548342	2026-06-11 20:19:39.556251
153	whatsapp	order_state	+34722891780	{"telefono": "+34722891780", "mensaje": "🎉 *¡Pedido recibido!*\\nTu pedido *OX-20260611-91614470* ya está registrado. Total: *€20.55*.\\n¡Estamos en ello ahora mismo! 🔥", "numero_pedido": "OX-20260611-91614470", "estado": "pendiente"}	sent	1	3	\N	\N	52	54	2026-06-11 22:16:30.314135	2026-06-11 22:16:30.333146
154	whatsapp	order_state	+34722891780	{"telefono": "+34722891780", "mensaje": "🎉 *¡Pedido recibido!*\\nTu pedido *OX-20260611-83072597* ya está registrado. Total: *€8.00*.\\n¡Estamos en ello ahora mismo! 🔥", "numero_pedido": "OX-20260611-83072597", "estado": "pendiente"}	sent	1	3	\N	\N	53	54	2026-06-11 22:17:24.921238	2026-06-11 22:17:24.929333
155	whatsapp	points_balance	+34722891780	{"telefono": "+34722891780", "mensaje": "⭐ *Tus puntos en Parcerito*\\n\\nTienes *0 puntos* disponibles.\\nEquivalen hasta a *€0.00* de descuento.\\n\\nPara canjearlos, arma tu pedido en la web y verifica este mismo WhatsApp durante la confirmación."}	sent	1	3	\N	\N	\N	54	2026-06-11 22:17:30.915192	2026-06-11 22:17:30.926372
156	whatsapp	points_balance	+34722891780	{"telefono": "+34722891780", "mensaje": "⭐ *Tus puntos en Parcerito*\\n\\nTienes *0 puntos* disponibles.\\nEquivalen hasta a *€0.00* de descuento.\\n\\nPara canjearlos, arma tu pedido en la web y verifica este mismo WhatsApp durante la confirmación."}	sent	1	3	\N	\N	\N	54	2026-06-11 22:17:32.891412	2026-06-11 22:17:32.902311
157	whatsapp	points_otp	+34622663874	{"telefono": "+34622663874", "mensaje": "🔐 *Código de verificación — El Parcerito de Carmona*\\n\\nTu código para canjear puntos por *1041 puntos* es:\\n\\n*143401*\\n\\n⏰ Válido 10 minutos. No lo compartas."}	sent	2	3	\N	\N	\N	39	2026-06-12 07:46:17.127284	2026-06-12 16:52:17.298402
158	whatsapp	points_otp	+34622663874	{"telefono": "+34622663874", "mensaje": "🔐 *Código de verificación — El Parcerito de Carmona*\\n\\nTu código para canjear puntos por *1041 puntos* es:\\n\\n*686915*\\n\\n⏰ Válido 10 minutos. No lo compartas."}	sent	2	3	\N	\N	\N	39	2026-06-12 07:57:06.014719	2026-06-12 16:52:18.332072
\.


--
-- Data for Name: order_events; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.order_events (id, pedido_id, tipo, estado_anterior, estado_nuevo, actor_id, canal, detalle, metadata_json, creado_en) FROM stdin;
136	38	pedido_creado	\N	pendiente	39	web	checkout web	{"zona_id": 2}	2026-06-09 15:37:25.87226
137	38	estado_cambiado	pendiente	armando	40	preparador	\N	\N	2026-06-09 15:46:37.893196
138	38	estado_cambiado	armando	listo	40	preparador	\N	\N	2026-06-09 15:46:46.670028
139	39	pedido_creado	\N	pendiente	39	web	checkout web	{"zona_id": 2}	2026-06-09 19:11:47.338839
159	48	pedido_creado	\N	pendiente	39	web	checkout web	{"zona_id": 2}	2026-06-09 22:06:03.663235
160	49	pedido_creado	\N	pendiente	49	web	checkout web	{"zona_id": 2}	2026-06-10 15:31:17.927735
161	39	estado_cambiado	pendiente	armando	1	admin	\N	\N	2026-06-11 16:44:45.822773
162	39	estado_cambiado	armando	listo	1	admin	\N	\N	2026-06-11 16:45:39.38362
163	49	estado_cambiado	pendiente	armando	1	admin	\N	\N	2026-06-11 16:45:42.129538
164	49	estado_cambiado	armando	listo	1	admin	\N	\N	2026-06-11 16:45:43.063231
165	49	estado_cambiado	listo	en_ruta	1	admin	\N	\N	2026-06-11 16:45:43.86336
166	39	estado_cambiado	listo	en_ruta	1	admin	\N	\N	2026-06-11 16:45:47.201403
167	48	estado_cambiado	pendiente	armando	1	admin	\N	\N	2026-06-11 16:47:05.974918
168	38	estado_cambiado	listo	en_ruta	1	admin	\N	\N	2026-06-11 16:48:18.799676
169	48	estado_cambiado	armando	listo	1	admin	\N	\N	2026-06-11 16:48:28.059259
170	49	estado_cambiado	en_ruta	entregado	1	admin	\N	\N	2026-06-11 16:48:39.329009
171	50	pedido_creado	\N	pendiente	39	web	checkout web	{"zona_id": 2}	2026-06-11 20:17:40.967606
172	51	pedido_creado	\N	pendiente	39	web	checkout web	{"zona_id": 2}	2026-06-11 20:19:39.532829
173	52	pedido_creado	\N	pendiente	54	web	checkout web	{"zona_id": 2}	2026-06-11 22:16:30.278904
174	53	pedido_creado	\N	pendiente	54	web	checkout web	{"zona_id": 2}	2026-06-11 22:17:24.90495
\.


--
-- Data for Name: order_items; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.order_items (id, pedido_id, producto_id, cantidad, precio_unit, subtotal, notas, metadata_json) FROM stdin;
37	38	12	1	2.00	2.00		{"producto": {"id": 12, "nombre": "colombiana", "descripcion": "refresco", "precio": 2.0, "precio_final": 2.0, "precio_costo": 0.5, "categoria_id": 5, "categoria_nombre": "GASEOSAS", "imagen_url": "https://imgs.search.brave.com/pbxulqec_xY9Gj3lLmiU5PcjLoWMcfIDm3Max1H-PoA/rs:fit:500:0:1:0/g:ce/aHR0cHM6Ly9qb3Rh/am90YWZvb2RzLmNv/bS93cC1jb250ZW50/L3VwbG9hZHMvMjAy/Mi8xMS9SRUYwMDAx/NC5qcGc", "origen_pais": "Colombia", "es_combo": false, "combo_precio_modo": null, "combo_descuento_pct": 0, "combo_precio_base": 0, "tipo_producto": "simple", "tipo_entrega": "inmediato", "stock_mostrar_en_web": true, "canjeable_con_puntos": true, "puntos_para_canje": 70, "es_hipoalergenico": true, "alergenos_json": null, "atributos": {}}}
38	38	13	1	2.00	2.00		{"producto": {"id": 13, "nombre": "uva postobon", "descripcion": "refresco", "precio": 2.0, "precio_final": 2.0, "precio_costo": 45.0, "categoria_id": 5, "categoria_nombre": "GASEOSAS", "imagen_url": "https://imgs.search.brave.com/k5TuiaOyPHwVbF3mPO-P_ozDIpBlHPXkEXXh4uQ0SJU/rs:fit:500:0:1:0/g:ce/aHR0cHM6Ly81c2Vu/dGlkb3MuZXMvd3At/Y29udGVudC91cGxv/YWRzLzIwMjMvMDgv/R2FzZW9zYS1Db2xv/bWJpYW5hLVBvc3Rv/Ym9uLURlLVV2YS1C/b3RlbGxhLVBsYXN0/aWNvLTUwME1MLmpw/Zw", "origen_pais": "Colombia", "es_combo": false, "combo_precio_modo": null, "combo_descuento_pct": 0, "combo_precio_base": 0, "tipo_producto": "simple", "tipo_entrega": "inmediato", "stock_mostrar_en_web": true, "canjeable_con_puntos": true, "puntos_para_canje": 67, "es_hipoalergenico": false, "alergenos_json": "[\\"gluten\\", \\"huevos\\", \\"lacteos\\"]", "atributos": {}}}
39	39	10	1	5.00	5.00		{"producto": {"id": 10, "nombre": "arepa de carne", "descripcion": "RICAS EMPANADAS DE CARNE", "precio": 5.0, "precio_final": 5.0, "precio_costo": 3.0, "categoria_id": 4, "categoria_nombre": "AREPAS", "imagen_url": "https://imgs.search.brave.com/lup7wU7zSrHtC50UyvJgpf50UaCVKsLuInGdFpIxt5w/rs:fit:500:0:1:0/g:ce/aHR0cHM6Ly9zdGF0/aWMuYmFpbmV0LmVz/L2NsaXAvNWE2M2Iy/MzctZjFjOC00ZDAy/LWIzNWEtYjI0NjEw/Yzk4OTY4X3NvdXJj/ZS1hc3BlY3QtcmF0/aW9fMTYwMHdfMC5q/cGc", "origen_pais": "Colombia", "es_combo": false, "combo_precio_modo": null, "combo_descuento_pct": 0, "combo_precio_base": 0, "tipo_producto": "simple", "tipo_entrega": "inmediato", "stock_mostrar_en_web": true, "canjeable_con_puntos": true, "puntos_para_canje": 89, "es_hipoalergenico": true, "alergenos_json": null, "atributos": {}}}
40	39	11	2	12.50	25.00		{"producto": {"id": 11, "nombre": "arepa de pollo", "descripcion": "ricas areaps de polo", "precio": 12.5, "precio_final": 12.5, "precio_costo": 4.0, "categoria_id": 4, "categoria_nombre": "AREPAS", "imagen_url": "https://imgs.search.brave.com/iGanNpknBjbAdkWsi9dS9PeEjWdBqMhSfmPCZ9iiAlU/rs:fit:500:0:1:0/g:ce/aHR0cHM6Ly9tb2pv/LmdlbmVyYWxtaWxs/cy5jb20vYXBpL3B1/YmxpYy9jb250ZW50/L0REMGU3TE91SVVt/YU1STUxhVVc3OXdf/Z21pX2hpX3Jlc19q/cGVnLmpwZWc_dj0y/OWU1MjQ3MSZ0PTE2/ZTNjZTI1MGYyNDQ2/NDhiZWYyOGM1OTQ5/ZmI5OWZm", "origen_pais": "Colombia", "es_combo": false, "combo_precio_modo": null, "combo_descuento_pct": 0, "combo_precio_base": 0, "tipo_producto": "simple", "tipo_entrega": "inmediato", "stock_mostrar_en_web": true, "canjeable_con_puntos": true, "puntos_para_canje": 89, "es_hipoalergenico": true, "alergenos_json": null, "atributos": {}}}
41	39	12	1	2.00	2.00		{"producto": {"id": 12, "nombre": "colombiana", "descripcion": "refresco", "precio": 2.0, "precio_final": 2.0, "precio_costo": 0.5, "categoria_id": 5, "categoria_nombre": "GASEOSAS", "imagen_url": "https://imgs.search.brave.com/pbxulqec_xY9Gj3lLmiU5PcjLoWMcfIDm3Max1H-PoA/rs:fit:500:0:1:0/g:ce/aHR0cHM6Ly9qb3Rh/am90YWZvb2RzLmNv/bS93cC1jb250ZW50/L3VwbG9hZHMvMjAy/Mi8xMS9SRUYwMDAx/NC5qcGc", "origen_pais": "Colombia", "es_combo": false, "combo_precio_modo": null, "combo_descuento_pct": 0, "combo_precio_base": 0, "tipo_producto": "simple", "tipo_entrega": "inmediato", "stock_mostrar_en_web": true, "canjeable_con_puntos": true, "puntos_para_canje": 70, "es_hipoalergenico": true, "alergenos_json": null, "atributos": {}}}
42	39	13	1	2.00	2.00		{"producto": {"id": 13, "nombre": "uva postobon", "descripcion": "refresco", "precio": 2.0, "precio_final": 2.0, "precio_costo": 45.0, "categoria_id": 5, "categoria_nombre": "GASEOSAS", "imagen_url": "https://imgs.search.brave.com/k5TuiaOyPHwVbF3mPO-P_ozDIpBlHPXkEXXh4uQ0SJU/rs:fit:500:0:1:0/g:ce/aHR0cHM6Ly81c2Vu/dGlkb3MuZXMvd3At/Y29udGVudC91cGxv/YWRzLzIwMjMvMDgv/R2FzZW9zYS1Db2xv/bWJpYW5hLVBvc3Rv/Ym9uLURlLVV2YS1C/b3RlbGxhLVBsYXN0/aWNvLTUwME1MLmpw/Zw", "origen_pais": "Colombia", "es_combo": false, "combo_precio_modo": null, "combo_descuento_pct": 0, "combo_precio_base": 0, "tipo_producto": "simple", "tipo_entrega": "inmediato", "stock_mostrar_en_web": true, "canjeable_con_puntos": true, "puntos_para_canje": 67, "es_hipoalergenico": false, "alergenos_json": "[\\"gluten\\", \\"huevos\\", \\"lacteos\\"]", "atributos": {}}}
64	51	12	1	2.00	2.00		{"producto": {"id": 12, "nombre": "colombiana", "descripcion": "refresco", "precio": 2.0, "precio_final": 2.0, "precio_costo": 0.5, "categoria_id": 5, "categoria_nombre": "GASEOSAS", "imagen_url": "https://imgs.search.brave.com/pbxulqec_xY9Gj3lLmiU5PcjLoWMcfIDm3Max1H-PoA/rs:fit:500:0:1:0/g:ce/aHR0cHM6Ly9qb3Rh/am90YWZvb2RzLmNv/bS93cC1jb250ZW50/L3VwbG9hZHMvMjAy/Mi8xMS9SRUYwMDAx/NC5qcGc", "origen_pais": "Colombia", "es_combo": false, "combo_precio_modo": null, "combo_descuento_pct": 0, "combo_precio_base": 0, "tipo_producto": "simple", "tipo_entrega": "inmediato", "canal_preparacion": "cocina", "proveedor_id": null, "stock_mostrar_en_web": true, "canjeable_con_puntos": true, "puntos_para_canje": 70, "es_hipoalergenico": true, "alergenos_json": null, "atributos": {}}}
56	48	10	1	5.00	5.00		{"producto": {"id": 10, "nombre": "arepa de carne", "descripcion": "RICAS EMPANADAS DE CARNE", "precio": 5.0, "precio_final": 5.0, "precio_costo": 3.0, "categoria_id": 4, "categoria_nombre": "AREPAS", "imagen_url": "https://imgs.search.brave.com/lup7wU7zSrHtC50UyvJgpf50UaCVKsLuInGdFpIxt5w/rs:fit:500:0:1:0/g:ce/aHR0cHM6Ly9zdGF0/aWMuYmFpbmV0LmVz/L2NsaXAvNWE2M2Iy/MzctZjFjOC00ZDAy/LWIzNWEtYjI0NjEw/Yzk4OTY4X3NvdXJj/ZS1hc3BlY3QtcmF0/aW9fMTYwMHdfMC5q/cGc", "origen_pais": "Colombia", "es_combo": false, "combo_precio_modo": null, "combo_descuento_pct": 0, "combo_precio_base": 0, "tipo_producto": "simple", "tipo_entrega": "inmediato", "stock_mostrar_en_web": true, "canjeable_con_puntos": true, "puntos_para_canje": 89, "es_hipoalergenico": true, "alergenos_json": null, "atributos": {}}}
57	48	12	1	2.00	2.00		{"producto": {"id": 12, "nombre": "colombiana", "descripcion": "refresco", "precio": 2.0, "precio_final": 2.0, "precio_costo": 0.5, "categoria_id": 5, "categoria_nombre": "GASEOSAS", "imagen_url": "https://imgs.search.brave.com/pbxulqec_xY9Gj3lLmiU5PcjLoWMcfIDm3Max1H-PoA/rs:fit:500:0:1:0/g:ce/aHR0cHM6Ly9qb3Rh/am90YWZvb2RzLmNv/bS93cC1jb250ZW50/L3VwbG9hZHMvMjAy/Mi8xMS9SRUYwMDAx/NC5qcGc", "origen_pais": "Colombia", "es_combo": false, "combo_precio_modo": null, "combo_descuento_pct": 0, "combo_precio_base": 0, "tipo_producto": "simple", "tipo_entrega": "inmediato", "stock_mostrar_en_web": true, "canjeable_con_puntos": true, "puntos_para_canje": 70, "es_hipoalergenico": true, "alergenos_json": null, "atributos": {}}}
58	48	11	1	0.00	0.00	\N	{"reward": {"tipo": "producto_puntos", "puntos": 89, "cliente_id": 39}, "producto": {"id": 11, "nombre": "arepa de pollo", "descripcion": "ricas areaps de polo", "precio": 12.5, "precio_final": 12.5, "precio_costo": 4.0, "categoria_id": 4, "categoria_nombre": "AREPAS", "imagen_url": "https://imgs.search.brave.com/iGanNpknBjbAdkWsi9dS9PeEjWdBqMhSfmPCZ9iiAlU/rs:fit:500:0:1:0/g:ce/aHR0cHM6Ly9tb2pv/LmdlbmVyYWxtaWxs/cy5jb20vYXBpL3B1/YmxpYy9jb250ZW50/L0REMGU3TE91SVVt/YU1STUxhVVc3OXdf/Z21pX2hpX3Jlc19q/cGVnLmpwZWc_dj0y/OWU1MjQ3MSZ0PTE2/ZTNjZTI1MGYyNDQ2/NDhiZWYyOGM1OTQ5/ZmI5OWZm", "origen_pais": "Colombia", "es_combo": false, "combo_precio_modo": null, "combo_descuento_pct": 0, "combo_precio_base": 0, "tipo_producto": "simple", "tipo_entrega": "inmediato", "stock_mostrar_en_web": true, "canjeable_con_puntos": true, "puntos_para_canje": 89, "es_hipoalergenico": true, "alergenos_json": null, "atributos": {}}}
59	49	14	2	17.55	35.10	1x arepa de carne | 1x arepa de pollo | bebidas: uva postobon	{"combo": {"extras_total": 0.0, "componentes": [{"combo_item_id": 28, "grupo_id": 3, "producto_id": 10, "nombre": "arepa de carne", "cantidad": 1, "fijo": true, "grupo": "Base incluida", "grupo_orden": 0, "notas_preparacion": ""}, {"combo_item_id": 29, "grupo_id": 3, "producto_id": 11, "nombre": "arepa de pollo", "cantidad": 1, "fijo": true, "grupo": "Base incluida", "grupo_orden": 0, "notas_preparacion": ""}], "selecciones": [{"grupo_id": 17, "grupo": "bebidas", "tipo": "seleccion", "orden": 2, "max_selecciones": 1, "opciones": [{"combo_item_id": 31, "grupo_id": 17, "producto_id": 13, "nombre": "uva postobon", "cantidad": 1, "qty": 1, "grupo_orden": 2, "precio_extra": 0.0, "extra_total": 0.0, "notas_preparacion": ""}]}]}, "producto": {"id": 14, "nombre": "tarde de chill", "descripcion": "empanaditas y  unos buenos refrescops", "precio": 17.55, "precio_final": 17.55, "precio_costo": 6.0, "categoria_id": 18, "categoria_nombre": "combos", "imagen_url": null, "origen_pais": "", "es_combo": true, "combo_precio_modo": "descuento_porcentaje", "combo_descuento_pct": 10.0, "combo_precio_base": 19.5, "tipo_producto": "combo", "tipo_entrega": "inmediato", "stock_mostrar_en_web": false, "canjeable_con_puntos": false, "puntos_para_canje": 0, "es_hipoalergenico": false, "alergenos_json": null, "atributos": {}}}
60	50	11	1	12.50	12.50		{"producto": {"id": 11, "nombre": "arepa de pollo", "descripcion": "ricas areaps de polo", "precio": 12.5, "precio_final": 12.5, "precio_costo": 4.0, "categoria_id": 4, "categoria_nombre": "AREPAS", "imagen_url": "https://imgs.search.brave.com/iGanNpknBjbAdkWsi9dS9PeEjWdBqMhSfmPCZ9iiAlU/rs:fit:500:0:1:0/g:ce/aHR0cHM6Ly9tb2pv/LmdlbmVyYWxtaWxs/cy5jb20vYXBpL3B1/YmxpYy9jb250ZW50/L0REMGU3TE91SVVt/YU1STUxhVVc3OXdf/Z21pX2hpX3Jlc19q/cGVnLmpwZWc_dj0y/OWU1MjQ3MSZ0PTE2/ZTNjZTI1MGYyNDQ2/NDhiZWYyOGM1OTQ5/ZmI5OWZm", "origen_pais": "Colombia", "es_combo": false, "combo_precio_modo": null, "combo_descuento_pct": 0, "combo_precio_base": 0, "tipo_producto": "simple", "tipo_entrega": "inmediato", "canal_preparacion": "cocina", "proveedor_id": null, "stock_mostrar_en_web": true, "canjeable_con_puntos": true, "puntos_para_canje": 89, "es_hipoalergenico": true, "alergenos_json": null, "atributos": {}}}
61	50	12	1	2.00	2.00		{"producto": {"id": 12, "nombre": "colombiana", "descripcion": "refresco", "precio": 2.0, "precio_final": 2.0, "precio_costo": 0.5, "categoria_id": 5, "categoria_nombre": "GASEOSAS", "imagen_url": "https://imgs.search.brave.com/pbxulqec_xY9Gj3lLmiU5PcjLoWMcfIDm3Max1H-PoA/rs:fit:500:0:1:0/g:ce/aHR0cHM6Ly9qb3Rh/am90YWZvb2RzLmNv/bS93cC1jb250ZW50/L3VwbG9hZHMvMjAy/Mi8xMS9SRUYwMDAx/NC5qcGc", "origen_pais": "Colombia", "es_combo": false, "combo_precio_modo": null, "combo_descuento_pct": 0, "combo_precio_base": 0, "tipo_producto": "simple", "tipo_entrega": "inmediato", "canal_preparacion": "cocina", "proveedor_id": null, "stock_mostrar_en_web": true, "canjeable_con_puntos": true, "puntos_para_canje": 70, "es_hipoalergenico": true, "alergenos_json": null, "atributos": {}}}
62	50	13	1	2.00	2.00		{"producto": {"id": 13, "nombre": "uva postobon", "descripcion": "refresco", "precio": 2.0, "precio_final": 2.0, "precio_costo": 45.0, "categoria_id": 5, "categoria_nombre": "GASEOSAS", "imagen_url": "https://imgs.search.brave.com/k5TuiaOyPHwVbF3mPO-P_ozDIpBlHPXkEXXh4uQ0SJU/rs:fit:500:0:1:0/g:ce/aHR0cHM6Ly81c2Vu/dGlkb3MuZXMvd3At/Y29udGVudC91cGxv/YWRzLzIwMjMvMDgv/R2FzZW9zYS1Db2xv/bWJpYW5hLVBvc3Rv/Ym9uLURlLVV2YS1C/b3RlbGxhLVBsYXN0/aWNvLTUwME1MLmpw/Zw", "origen_pais": "Colombia", "es_combo": false, "combo_precio_modo": null, "combo_descuento_pct": 0, "combo_precio_base": 0, "tipo_producto": "simple", "tipo_entrega": "inmediato", "canal_preparacion": "cocina", "proveedor_id": null, "stock_mostrar_en_web": true, "canjeable_con_puntos": true, "puntos_para_canje": 67, "es_hipoalergenico": false, "alergenos_json": "[\\"gluten\\", \\"huevos\\", \\"lacteos\\"]", "atributos": {}}}
63	51	11	1	12.50	12.50		{"producto": {"id": 11, "nombre": "arepa de pollo", "descripcion": "ricas areaps de polo", "precio": 12.5, "precio_final": 12.5, "precio_costo": 4.0, "categoria_id": 4, "categoria_nombre": "AREPAS", "imagen_url": "https://imgs.search.brave.com/iGanNpknBjbAdkWsi9dS9PeEjWdBqMhSfmPCZ9iiAlU/rs:fit:500:0:1:0/g:ce/aHR0cHM6Ly9tb2pv/LmdlbmVyYWxtaWxs/cy5jb20vYXBpL3B1/YmxpYy9jb250ZW50/L0REMGU3TE91SVVt/YU1STUxhVVc3OXdf/Z21pX2hpX3Jlc19q/cGVnLmpwZWc_dj0y/OWU1MjQ3MSZ0PTE2/ZTNjZTI1MGYyNDQ2/NDhiZWYyOGM1OTQ5/ZmI5OWZm", "origen_pais": "Colombia", "es_combo": false, "combo_precio_modo": null, "combo_descuento_pct": 0, "combo_precio_base": 0, "tipo_producto": "simple", "tipo_entrega": "inmediato", "canal_preparacion": "cocina", "proveedor_id": null, "stock_mostrar_en_web": true, "canjeable_con_puntos": true, "puntos_para_canje": 89, "es_hipoalergenico": true, "alergenos_json": null, "atributos": {}}}
65	52	14	1	17.55	17.55	1x arepa de carne | 1x arepa de pollo | bebidas: colombiana	{"combo": {"extras_total": 0.0, "componentes": [{"combo_item_id": 28, "grupo_id": 3, "producto_id": 10, "nombre": "arepa de carne", "cantidad": 1, "fijo": true, "grupo": "Base incluida", "grupo_orden": 0, "notas_preparacion": ""}, {"combo_item_id": 29, "grupo_id": 3, "producto_id": 11, "nombre": "arepa de pollo", "cantidad": 1, "fijo": true, "grupo": "Base incluida", "grupo_orden": 0, "notas_preparacion": ""}], "selecciones": [{"grupo_id": 17, "grupo": "bebidas", "tipo": "seleccion", "orden": 2, "max_selecciones": 1, "opciones": [{"combo_item_id": 32, "grupo_id": 17, "producto_id": 12, "nombre": "colombiana", "cantidad": 1, "qty": 1, "grupo_orden": 2, "precio_extra": 0.0, "extra_total": 0.0, "notas_preparacion": ""}]}]}, "producto": {"id": 14, "nombre": "tarde de chill", "descripcion": "empanaditas y  unos buenos refrescops", "precio": 17.55, "precio_final": 17.55, "precio_costo": 6.0, "categoria_id": 18, "categoria_nombre": "combos", "imagen_url": null, "origen_pais": "", "es_combo": true, "combo_precio_modo": "descuento_porcentaje", "combo_descuento_pct": 10.0, "combo_precio_base": 19.5, "tipo_producto": "combo", "tipo_entrega": "inmediato", "canal_preparacion": "cocina", "proveedor_id": null, "stock_mostrar_en_web": false, "canjeable_con_puntos": false, "puntos_para_canje": 0, "es_hipoalergenico": false, "alergenos_json": null, "atributos": {}}}
66	53	10	1	5.00	5.00		{"producto": {"id": 10, "nombre": "arepa de carne", "descripcion": "RICAS EMPANADAS DE CARNE", "precio": 5.0, "precio_final": 5.0, "precio_costo": 3.0, "categoria_id": 4, "categoria_nombre": "AREPAS", "imagen_url": "https://imgs.search.brave.com/lup7wU7zSrHtC50UyvJgpf50UaCVKsLuInGdFpIxt5w/rs:fit:500:0:1:0/g:ce/aHR0cHM6Ly9zdGF0/aWMuYmFpbmV0LmVz/L2NsaXAvNWE2M2Iy/MzctZjFjOC00ZDAy/LWIzNWEtYjI0NjEw/Yzk4OTY4X3NvdXJj/ZS1hc3BlY3QtcmF0/aW9fMTYwMHdfMC5q/cGc", "origen_pais": "Colombia", "es_combo": false, "combo_precio_modo": null, "combo_descuento_pct": 0, "combo_precio_base": 0, "tipo_producto": "simple", "tipo_entrega": "inmediato", "canal_preparacion": "cocina", "proveedor_id": null, "stock_mostrar_en_web": true, "canjeable_con_puntos": true, "puntos_para_canje": 89, "es_hipoalergenico": true, "alergenos_json": null, "atributos": {}}}
\.


--
-- Data for Name: order_provider_status; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.order_provider_status (id, pedido_id, proveedor_id, preparado, preparado_en, actualizado_por) FROM stdin;
\.


--
-- Data for Name: orders; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.orders (id, numero_pedido, cliente_id, estado, origen, subtotal, descuento, total, cupon_id, puntos_usados, puntos_ganados, metodo_pago, direccion_entrega, notas, preparador_id, repartidor_id, cajero_id, creado_en, entregado_en, zona_id, afiliado_codigo_id, es_entrega_epicentro, codigo_confirmacion, codigo_confirmado_en, intentos_codigo, pago_confirmado, pago_confirmado_por, pago_confirmado_en, whatsapp_enviado_confirmacion, resena_calificacion, resena_comentario, resena_enviada, proveedor_preparado, proveedor_preparado_en) FROM stdin;
50	OX-20260611-40636213	39	pendiente	online	16.50	0.00	19.50	\N	0	19	efectivo	calle, andalucia 20		3	\N	\N	2026-06-11 20:17:40.964159	\N	2	\N	t	\N	\N	0	f	\N	\N	f	\N	\N	f	f	\N
51	OX-20260611-00930280	39	pendiente	online	14.50	0.00	17.50	\N	0	17	efectivo	calle, andalucia 20		3	\N	\N	2026-06-11 20:19:39.531518	\N	2	\N	t	\N	\N	0	f	\N	\N	f	\N	\N	f	f	\N
52	OX-20260611-91614470	54	pendiente	online	17.55	0.00	20.55	\N	0	20	efectivo	calle torre del oro  32 1A	[Combo 14: bebidas: colombiana]	3	\N	\N	2026-06-11 22:16:30.276622	\N	2	\N	t	\N	\N	0	f	\N	\N	f	\N	\N	f	f	\N
53	OX-20260611-83072597	54	pendiente	online	5.00	0.00	8.00	\N	0	8	efectivo	calle torre del oro  32 1A		3	\N	\N	2026-06-11 22:17:24.902515	\N	2	\N	t	\N	\N	0	f	\N	\N	f	\N	\N	f	f	\N
48	OX-20260609-42251390	39	listo	online	7.00	3.20	6.80	\N	409	6	efectivo	calle, andalucia 20		40	\N	\N	2026-06-09 22:06:03.659157	\N	2	\N	t	\N	\N	0	f	\N	\N	f	\N	\N	f	f	\N
39	OX-20260609-00843267	39	en_ruta	online	34.00	0.00	37.00	\N	0	37	efectivo	calle, andalucia 20		40	\N	\N	2026-06-09 19:11:47.334274	\N	2	\N	t	804780	\N	0	f	\N	\N	f	\N	\N	f	f	\N
38	OX-20260609-17784111	39	en_ruta	online	4.00	0.00	7.00	\N	0	7	efectivo	calle, andalucia 20	[Combo 91: Elige 1 bebida: uva postobon]	40	\N	\N	2026-06-09 15:37:25.869826	\N	2	\N	t	368649	\N	0	f	\N	\N	f	\N	\N	f	f	\N
49	OX-20260610-18324098	49	entregado	online	35.10	0.00	38.10	\N	0	38	efectivo	calle, ramon de olla 9	[Combo 14: bebidas: uva postobon]	40	\N	\N	2026-06-10 15:31:17.925395	2026-06-11 16:48:39.321134	2	\N	t	025839	\N	0	f	\N	\N	f	\N	\N	t	f	\N
\.


--
-- Data for Name: points_log; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.points_log (id, cliente_id, pedido_id, tipo, cantidad, descripcion, creado_en) FROM stdin;
35	39	38	ganado	7	Pedido OX-20260609-17784111	2026-06-09 15:37:25.883504
36	39	\N	ajuste	1000	Ajuste por WhatsApp admin 34622663874	2026-06-09 15:38:36.391949
37	39	39	ganado	37	Pedido OX-20260609-00843267	2026-06-09 19:11:47.361546
47	39	\N	ajuste	400	Ajuste por WhatsApp admin 34622663874	2026-06-09 22:04:46.111193
48	39	48	canjeado	-320	Canje en pedido	2026-06-09 22:06:03.682528
49	39	48	canjeado	-89	Canje en pedido	2026-06-09 22:06:03.685393
50	39	48	ganado	6	Pedido OX-20260609-42251390	2026-06-09 22:06:03.693326
51	49	49	ganado	38	Pedido OX-20260610-18324098	2026-06-10 15:31:17.944491
\.


--
-- Data for Name: price_history; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.price_history (id, producto_id, precio_anterior, precio_nuevo, cambiado_por, cambiado_en, motivo) FROM stdin;
1	11	7.00	3.00	\N	2026-06-03 21:31:02.946557	Cambio por WhatsApp admin 34622663874
2	11	3.00	12.50	\N	2026-06-04 19:56:32.190606	Cambio por WhatsApp admin 34622663874
\.


--
-- Data for Name: products; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.products (id, nombre, descripcion, precio, precio_costo, categoria_id, imagen_url, origen_pais, es_combo, tipo_producto, atributos_json, activo, creado_en, tipo_entrega, fecha_llegada, dias_anticipacion_encargo, hora_inicio_visibilidad, hora_fin_visibilidad, dias_semana_json, stock_mostrar_en_web, canjeable_con_puntos, puntos_para_canje, es_hipoalergenico, alergenos_info, alergenos_json, combo_precio_modo, combo_descuento_pct, combo_precio_base, canal_preparacion, proveedor_id) FROM stdin;
14	tarde de chill	empanaditas y  unos buenos refrescops	17.55	6.00	18	\N		t	combo	\N	t	2026-06-02 15:36:30.759904	inmediato	\N	1	\N	\N	[0, 1, 2, 3, 4, 5, 6]	f	f	\N	f	\N	\N	descuento_porcentaje	10.00	19.50	cocina	\N
107	Agua Mineral (test)	\N	1.50	0.50	4	\N	\N	f	simple	\N	t	2026-06-12 16:28:30.241152	inmediato	\N	1	\N	\N	\N	f	f	\N	f	\N	\N	fijo	0.00	0.00	almacen	\N
108	Torta Especial (test)	\N	15.00	6.00	4	\N	\N	f	simple	\N	t	2026-06-12 16:28:30.245186	programado	2026-06-12	1	\N	\N	\N	f	f	\N	f	\N	\N	fijo	0.00	0.00	cocina	\N
109	Mojito del Bar (test)	\N	8.00	2.50	4	\N	\N	f	simple	\N	t	2026-06-12 16:28:30.247108	inmediato	\N	1	\N	\N	\N	f	f	\N	f	\N	\N	fijo	0.00	0.00	cocina	55
10	arepa de carne	RICAS EMPANADAS DE CARNE	5.00	3.00	4	https://imgs.search.brave.com/lup7wU7zSrHtC50UyvJgpf50UaCVKsLuInGdFpIxt5w/rs:fit:500:0:1:0/g:ce/aHR0cHM6Ly9zdGF0/aWMuYmFpbmV0LmVz/L2NsaXAvNWE2M2Iy/MzctZjFjOC00ZDAy/LWIzNWEtYjI0NjEw/Yzk4OTY4X3NvdXJj/ZS1hc3BlY3QtcmF0/aW9fMTYwMHdfMC5q/cGc	Colombia	f	simple	\N	t	2026-06-02 15:04:21.409673	inmediato	\N	1	07:00:00	00:00:00	[0, 1, 2, 3, 4, 5, 6]	t	t	89	t	\N	\N	fijo	0.00	0.00	cocina	\N
12	colombiana	refresco	2.00	0.50	5	https://imgs.search.brave.com/pbxulqec_xY9Gj3lLmiU5PcjLoWMcfIDm3Max1H-PoA/rs:fit:500:0:1:0/g:ce/aHR0cHM6Ly9qb3Rh/am90YWZvb2RzLmNv/bS93cC1jb250ZW50/L3VwbG9hZHMvMjAy/Mi8xMS9SRUYwMDAx/NC5qcGc	Colombia	f	simple	\N	t	2026-06-02 15:33:31.957387	inmediato	\N	1	05:33:00	00:33:00	[0, 1, 2, 3, 4, 5, 6]	t	t	70	t	\N	\N	fijo	0.00	0.00	cocina	\N
13	uva postobon	refresco	2.00	45.00	5	https://imgs.search.brave.com/k5TuiaOyPHwVbF3mPO-P_ozDIpBlHPXkEXXh4uQ0SJU/rs:fit:500:0:1:0/g:ce/aHR0cHM6Ly81c2Vu/dGlkb3MuZXMvd3At/Y29udGVudC91cGxv/YWRzLzIwMjMvMDgv/R2FzZW9zYS1Db2xv/bWJpYW5hLVBvc3Rv/Ym9uLURlLVV2YS1C/b3RlbGxhLVBsYXN0/aWNvLTUwME1MLmpw/Zw	Colombia	f	simple	\N	t	2026-06-02 15:34:56.499978	inmediato	\N	1	01:34:00	00:34:00	[0, 1, 2, 3, 4, 5, 6]	t	t	67	f	\N	["gluten", "huevos", "lacteos"]	fijo	0.00	0.00	cocina	\N
11	arepa de pollo	ricas areaps de polo	12.50	4.00	4	https://imgs.search.brave.com/iGanNpknBjbAdkWsi9dS9PeEjWdBqMhSfmPCZ9iiAlU/rs:fit:500:0:1:0/g:ce/aHR0cHM6Ly9tb2pv/LmdlbmVyYWxtaWxs/cy5jb20vYXBpL3B1/YmxpYy9jb250ZW50/L0REMGU3TE91SVVt/YU1STUxhVVc3OXdf/Z21pX2hpX3Jlc19q/cGVnLmpwZWc_dj0y/OWU1MjQ3MSZ0PTE2/ZTNjZTI1MGYyNDQ2/NDhiZWYyOGM1OTQ5/ZmI5OWZm	Colombia	f	simple	\N	t	2026-06-02 15:31:21.050848	inmediato	\N	1	07:30:00	00:00:00	[0, 1, 2, 3, 4, 5, 6]	t	t	89	t	\N	\N	fijo	0.00	0.00	cocina	\N
\.


--
-- Data for Name: push_subscriptions; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.push_subscriptions (id, user_id, endpoint, p256dh, auth, rol, user_agent, creado_en, ultimo_uso, activo) FROM stdin;
\.


--
-- Data for Name: reviews; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.reviews (id, producto_id, cliente_id, pedido_id, calificacion, comentario, aprobada, creado_en) FROM stdin;
\.


--
-- Data for Name: schema_migrations; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.schema_migrations (id, description, applied_at) FROM stdin;
20260526_01_order_events_notification_outbox	Crear order_events y notification_outbox	2026-06-02 13:09:12.794102
20260526_02_site_config_valor_text	Ampliar site_config.valor a TEXT para claves e integraciones largas	2026-06-02 13:09:12.797729
20260602_01_products_combo_pricing	Agregar modo de precio y descuento porcentual para combos	2026-06-02 14:50:06.499645
20260605_01_combo_groups_structure	Crear secciones formales de combo y enlazarlas a componentes	2026-06-05 16:35:45.214097
20260605_02_combo_groups_backfill	Poblar secciones formales para combos existentes sin grupos	2026-06-05 16:41:12.57617
20260605_03_empty_combo_base_group	Crear seccion inicial para combos sin componentes	2026-06-05 16:42:33.687567
20260605_04_combo_item_option_fields	Agregar suplementos, default, activo y notas operativas a opciones de combo	2026-06-05 17:01:17.240984
20260608_01_remove_legacy_promotions	Retirar tabla y columnas del motor de promociones obsoleto	2026-06-08 11:01:51.269515
20260609_01_unique_customer_phone	Normalizar y hacer único el teléfono usado como identidad del cliente	2026-06-09 15:08:23.758961
20260612_01_provider_kitchen_flow	Agregar canal, proveedor y preparación independiente por proveedor	2026-06-12 16:51:34.590053
20260613_01_financial_uniqueness	Impedir comisiones, ingresos y pagos staff duplicados	2026-06-13 12:11:59.268519
20260613_02_financial_uniqueness	Impedir comisiones, ingresos, pagos staff y puntos duplicados	2026-06-13 17:44:06.366453
20260615_01_affiliate_payment_integrity	Separar comisiones, enlazar usos de afiliado e impedir devoluciones duplicadas	2026-06-15 14:04:40.15791
\.


--
-- Data for Name: site_config; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.site_config (id, clave, valor, descripcion, actualizado_en, actualizado_por) FROM stdin;
1	PUNTOS_POR_EURO	1	Puntos por euro gastado	2026-05-22 23:30:02.912831	\N
2	PUNTOS_CANJE_RATIO	100	100 puntos = 1 euro de descuento	2026-05-22 23:30:02.914846	\N
3	CART_MAX_QTY	99	Cantidad máxima por producto en carrito	2026-05-22 23:30:02.916692	\N
4	ALERTA_CADUCIDAD_DIAS	7	Dias antes de caducidad para alerta	2026-05-22 23:30:02.918448	\N
20	CENTRO_LAT	37.4698	Latitud del centro de Carmona (geocodificación)	2026-05-22 23:30:02.94432	\N
21	CENTRO_LON	-5.6435	Longitud del centro de Carmona (geocodificación)	2026-05-22 23:30:02.945632	\N
24	VAPID_PUBLIC_KEY	BFNhBYPn9A9YS4_1tGJWGmMfFjYTDgll7xfqiYl1yPwHKY3Frojkw-T4fAxadf5_oEc6Um-jUgVu82HXQpdaKcI	Clave pública VAPID para Web Push	2026-05-22 23:30:03.662592	\N
25	VAPID_PRIVATE_KEY	MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgXTVHyQv9gYT98tHm6SG5LWSp8BNLXizW8w9g5FPihyKhRANCAARTYQWD5_QPWEuP9bRiVhpjHxY2Ew4JZe8X6omJdcj8BymNxa6I5MPk-HwMWnX-f6BHOlJvo1IFbvNh10KXWinC	Clave privada VAPID para Web Push (NO compartir)	2026-05-22 23:30:03.66372	\N
31	COMBO_MAX_SELECTIONS_GROUP	10	Máximo de selecciones permitidas por grupo elegible	2026-06-02 13:09:11.376223	\N
32	COMBO_MAX_DISCOUNT_PCT	50	Descuento porcentual máximo permitido para combos	2026-06-02 13:09:11.377497	\N
33	BOT_ALLOW_ORDER_CREATE	0	Permitir crear pedidos desde chatbot (1/0). Por defecto el bot solo consulta.	2026-06-02 13:09:11.382316	\N
38	BOT_EMAIL_DOMAIN	wa.internal	Dominio para emails auto-generados de clientes WhatsApp	2026-06-02 13:09:11.390889	\N
39	CIUDAD_NEGOCIO	Carmona	Ciudad del negocio (para geocodificación de direcciones)	2026-06-02 13:09:11.399811	\N
40	PROVINCIA_NEGOCIO	Sevilla	Provincia del negocio (para geocodificación estructurada)	2026-06-02 13:09:11.401456	\N
26	BOT_PUBLIC_URL	http://localhost:3000	\N	2026-05-23 16:52:55.756872	\N
57	SLOGAN_NEGOCIO	Comida latina hecha al momento	Eslogan o tagline del negocio	2026-06-09 22:02:07.573793	\N
58	DESCRIPCION_NEGOCIO	Restaurante latino de cocina casera en Carmona, Sevilla	Descripción breve del negocio (SEO)	2026-06-09 22:02:07.573793	\N
12	LOGO_URL	/uploads/logo/logo.png	URL del logo del negocio	2026-06-02 14:56:26.776641	\N
42	APP_ICON_URL	/uploads/icon/app-icon.png	Icono / favicon de la app	2026-06-02 14:56:37.766485	1
11	OXIDIAN_PUBLIC_URL	http://192.168.1.41:5070	URL de Oxidian que usará el bot	2026-06-16 14:16:26.731343	\N
14	COLOR_PRIMARIO	#D9961A	Color principal de marca	2026-06-02 14:21:52.643648	\N
15	COLOR_SECUNDARIO	#CE1126	Color secundario de marca	2026-06-02 14:21:52.644783	\N
16	COLOR_ACENTO	#003087	Color de acento para estado y CTA	2026-06-02 14:21:52.64585	\N
27	PWA_VERSION	2026-06-02	\N	2026-06-02 14:21:52.646897	\N
28	COMBO_MIN_COMPONENTS	1	Mínimo de componentes requeridos para crear un combo	2026-06-02 13:09:11.366411	\N
29	COMBO_MAX_COMPONENTS	30	Máximo de componentes permitidos por combo	2026-06-02 13:09:11.373336	\N
22	RADIO_ENTREGA_KM	15	Radio máximo de entrega en km desde el centro	2026-06-09 01:08:01.412355	1
19	TIENDA_FORZAR_CERRADA	0	Forzar tienda cerrada (1/0)	2026-06-16 14:12:41.636436	\N
17	HORARIO_APERTURA	09:00	Hora de apertura tienda (HH:MM)	2026-06-16 14:12:41.638114	\N
30	COMBO_MAX_QTY_COMPONENT	50	Cantidad máxima por componente dentro de un combo	2026-06-02 13:09:11.374838	\N
13	TIENDA_URL	http://192.168.1.41:5070	URL de la tienda web para pedidos (mostrado en WhatsApp)	2026-06-16 14:16:26.732237	\N
18	HORARIO_CIERRE	22:30	Hora de cierre tienda (HH:MM)	2026-06-16 14:12:41.639968	\N
5	NOMBRE_NEGOCIO	El Parcerito de Carmona	Nombre del negocio	2026-06-16 14:16:26.733163	\N
7	TELEFONO_NEGOCIO	+34622663874	Telefono de contacto	2026-06-16 14:16:26.734101	\N
23	VALIDAR_RADIO_ENTREGA	1	Activar validación de radio de entrega (1/0)	2026-06-16 14:12:41.641824	\N
6	DIRECCION_NEGOCIO	Carmona, Sevilla	Direccion del local	2026-06-16 14:16:26.734999	\N
34	EVOLUTION_API_URL	http://evolution-api:8080	URL interna de Evolution API	2026-06-16 14:16:26.735874	\N
41	BLOQUEAR_DIRECCION_NO_VERIFICADA	1	Bloquear pedido si no se puede geocodificar la dirección (1/0)	2026-06-16 14:12:41.643643	\N
35	EVOLUTION_API_KEY	eLmX3fqUuFA4MRep6IcV-NcIc6CioJlSVicRqJVaNqw	Clave API de Evolution	2026-06-16 14:16:26.736787	\N
36	EVOLUTION_INSTANCE	parcerito	Instancia WhatsApp de Evolution	2026-06-16 14:16:26.737681	\N
37	WEBHOOK_SECRET	57bb87d09a4ebc4fa47bddc0ce67735b91dc6fe7a81c89fd5d5705449068b074	Secreto webhook Evolution -> Oxidian	2026-06-16 14:16:26.738578	\N
9	BOT_API_URL	http://127.0.0.1:3000	URL del bot WhatsApp (docker: http://chatbot:3000)	2026-06-16 14:16:26.72743	\N
8	BOT_API_KEY	6qpTMSELwLVo-w9M8Yh0IXzLDJ4	API key compartida entre Oxidian y el bot	2026-06-16 14:16:26.729551	\N
10	BOT_PANEL_KEY	p1E8VlMCXeSsSJfFfilWrOeCmN0	Clave para administrar el bot desde Super Admin	2026-06-16 14:16:26.730478	\N
\.


--
-- Data for Name: staff_payments; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.staff_payments (id, user_id, tipo, monto, concepto, periodo_inicio, periodo_fin, pedido_id, pagado, fecha_pago, registrado_por, creado_en, origen) FROM stdin;
\.


--
-- Data for Name: stock; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.stock (id, producto_id, cantidad, unidad, lote, fecha_entrada, fecha_caducidad, alerta_dias, ubicacion) FROM stdin;
4	13	55	unidad	\N	2026-06-02	\N	7	\N
2	11	99	unidad	\N	2026-06-02	\N	7	\N
3	12	44	unidad	\N	2026-06-02	\N	7	\N
1	10	98	unidad	\N	2026-06-02	\N	7	\N
\.


--
-- Data for Name: users; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.users (id, nombre, email, password_hash, rol, telefono, direccion, puntos, activo, creado_en, last_seen, en_linea, cod_puntos, cod_puntos_expira, cod_puntos_intentos, puesto_trabajo, salario_base, tarifa_entrega, telefono_normalizado) FROM stdin;
3	Armado de Pedidos	preparacion@oxidian.com	scrypt:32768:8:1$1YnRwDKNQ00P7Gxq$6f97e5e4e2dbcb3b8d625c0b5d735a71ac6e6e008b9ada7c5b94e300ef1d6d0a0bfc401eb52550aceb8f14c2afe6e1d6224521243b1392699b8aefe0bb530040	preparacion	\N	\N	0	t	2026-05-22 23:30:03.136697	2026-06-09 20:10:34.830027	f	\N	\N	0	Armado de pedidos delivery	0.00	0.00	\N
52	Ana Staff	staff@oxidian.com	scrypt:32768:8:1$bjlpVrmm8L4XNyqp$65985c49d2bdb13d77ed37836044b704d6b4daceed8a8376f8c80e45eda908de1e77dd2a310390ec65b183319c1096afce28fdefa6ff5319e1b4fc00e2269589	staff	\N	\N	0	t	2026-06-11 20:08:42.954923	2026-06-11 20:08:42.868325	f	\N	\N	0	Caja e inventario	0.00	0.00	\N
5	Repartidor Delivery	repartidor@oxidian.com	scrypt:32768:8:1$gWIiPgwN8fsE5gNF$0855165fefa78c68c44220a8afcd333bd04e16c28e1ab778502ee165aad62d42744e2aafbd18089f1a9d83d7645da6579a5e24505ee8b1967674be557b2d12c1	repartidor	\N	\N	0	t	2026-05-22 23:30:03.307577	2026-06-09 20:10:36.529386	f	\N	\N	0	Reparto delivery	0.00	2.50	\N
39	steven	tel.+34622663874@wa.internal	scrypt:32768:8:1$as5CKB2dNuoXF0nx$57c6f0ecf2d2ffccb118330101ed552723b84bb5c8b8521016d940ddf7bcabef6a1a4ac2b9d981ebc3e43fbfb9bb793bf5bc8ee43580b795dc1ee3c5c79d6b90	cliente	+34622663874	calle, andalucia 20	1041	f	2026-06-09 15:37:25.86521	\N	f	686915	2026-06-12 08:07:05.997642	0	\N	0.00	0.00	+34622663874
1	Super Admin	carmocream15@gmail.com	scrypt:32768:8:1$HUxoMg1QL1rjMRsz$d60a65a4136a5c42487474d1a37c8cc8a357e832acf5ea96f9fbfdedef781e08ea8850e3b24883184b314a13eb9e71e1b59533c5eb02c5eb25cc176257f7a4de	super_admin	\N	\N	0	t	2026-05-22 23:30:02.907491	2026-06-16 14:56:58.310137	t	\N	\N	0	\N	0.00	0.00	\N
54	brandon	tel.+34722891780@wa.internal	scrypt:32768:8:1$Txqxd6KBea9tchpb$ebb49995b94fea2eeccd9d9e84748d9b6be6476ec41e72bc613c31c00d2c736ae9aea60893c5545d2faf5236a53d86d8007eab0ae30dbd315b7b378a9c58b063	cliente	+34722891780	calle torre del oro  32 1A	0	t	2026-06-11 22:16:30.272331	\N	f	\N	\N	0	\N	0.00	0.00	+34722891780
49	steven	tel.+34622663974@wa.internal	scrypt:32768:8:1$fvTMLePiOZ9CD8le$b3f988be946e8ad023791510b83270cffaef046283b7c550105db27217dd69348d9349f5e1c7bde5954c608b65e05b16a605982e089a43d84cfbb704f979965e	cliente	+34622663974	calle, ramon de olla 9	38	t	2026-06-10 15:31:17.920451	\N	f	\N	\N	0	\N	0.00	0.00	+34622663974
51	Admin	admin@oxidian.com	scrypt:32768:8:1$gpGQO8Ak8IkALxmC$c4e1dad02f86ac371d547b461319a521c02f8a0be4752b94ebffd7b0a0142fd761e0c352448be18bb92af6d4aa2d42dbd4ec630809d3f860d2fca1d2715d1f1a	admin	\N	\N	0	t	2026-06-11 20:08:42.837126	2026-06-15 14:32:39.748256	f	\N	\N	0	\N	0.00	0.00	\N
55	Bar El Parcerito	bar@oxidian.com	scrypt:32768:8:1$YOeePZ7A6EvaIZKU$539e4d136dba375f1e5d507886518f521bee597299b30ca24c3896bf7e869caaa68545ed0f15a774cf8cd985f292b6afbf4f810b9b15c2a4aa4ced7ae318316f	proveedor	\N	\N	0	t	2026-06-12 16:28:30.209355	2026-06-16 14:19:45.824503	f	\N	\N	0	\N	0.00	0.00	\N
53	Super Admin	superadmin@oxidian.local	scrypt:32768:8:1$op22A7gOnTuWPReL$6b46a5d2ced15acb5e14c5be39b2c9a52f670fc7c41c24bdbf8f89ed8dd83755a25deeeb64890fe00890a7596f41497e9d20896488879c008b97a1247191211b	super_admin	\N	\N	0	t	2026-06-11 20:14:40.247253	2026-06-16 11:48:00.790821	f	\N	\N	0	\N	0.00	0.00	\N
40	Chef Carlos	cocina@oxidian.com	scrypt:32768:8:1$ftQRnfIcxNxdo22F$2c65c1fc3294d0170f2d0850314661f9c4fa82198b20758db2494fbf4d77886130ef7e33df2a56a9da26f859b78419589d7a53065a7ce4a9d784bfc1eb93fd21	cocina	\N	\N	0	t	2026-06-09 15:45:57.174965	2026-06-09 15:48:32.091078	f	\N	\N	0	Cocina	1200.00	0.00	\N
50	Super Admin	superadmin@oxidian.com	scrypt:32768:8:1$aEXBasMr73Xga5pd$cc14e8e09fb1ad6a3355be4688420136c16a233a45bcf6769a97049f898a35b3dee00d641a19a55669f1e4ead8cef7db80abc6929c68fdd7601fd9d07f52d605	super_admin	\N	\N	0	t	2026-06-11 20:08:42.75396	2026-06-16 13:58:47.531841	f	\N	\N	0	\N	0.00	0.00	\N
\.


--
-- Data for Name: zonas_entrega; Type: TABLE DATA; Schema: public; Owner: oxidian
--

COPY public.zonas_entrega (id, nombre, descripcion, es_epicentro, activo, precio_envio, tiempo_estimado_min, gratis_desde, orden) FROM stdin;
2	Carmona y alrededores	Cobertura local con tarifa base configurable	t	t	3.00	30	\N	1
\.


--
-- Name: admin_features_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.admin_features_id_seq', 22, true);


--
-- Name: affiliate_codes_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.affiliate_codes_id_seq', 2, true);


--
-- Name: affiliate_uses_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.affiliate_uses_id_seq', 1, true);


--
-- Name: audit_log_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.audit_log_id_seq', 65, true);


--
-- Name: caja_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.caja_id_seq', 87, true);


--
-- Name: campanas_marketing_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.campanas_marketing_id_seq', 1, false);


--
-- Name: categorias_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.categorias_id_seq', 61, true);


--
-- Name: combo_groups_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.combo_groups_id_seq', 98, true);


--
-- Name: combo_items_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.combo_items_id_seq', 118, true);


--
-- Name: coupons_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.coupons_id_seq', 1, true);


--
-- Name: menu_config_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.menu_config_id_seq', 38, true);


--
-- Name: notification_outbox_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.notification_outbox_id_seq', 287, true);


--
-- Name: order_events_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.order_events_id_seq', 316, true);


--
-- Name: order_items_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.order_items_id_seq', 150, true);


--
-- Name: order_provider_status_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.order_provider_status_id_seq', 9, true);


--
-- Name: orders_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.orders_id_seq', 113, true);


--
-- Name: points_log_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.points_log_id_seq', 106, true);


--
-- Name: price_history_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.price_history_id_seq', 2, true);


--
-- Name: products_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.products_id_seq', 178, true);


--
-- Name: push_subscriptions_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.push_subscriptions_id_seq', 1, false);


--
-- Name: reviews_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.reviews_id_seq', 1, false);


--
-- Name: site_config_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.site_config_id_seq', 85, true);


--
-- Name: staff_payments_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.staff_payments_id_seq', 38, true);


--
-- Name: stock_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.stock_id_seq', 97, true);


--
-- Name: users_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.users_id_seq', 96, true);


--
-- Name: zonas_entrega_id_seq; Type: SEQUENCE SET; Schema: public; Owner: oxidian
--

SELECT pg_catalog.setval('public.zonas_entrega_id_seq', 2, true);


--
-- Name: admin_features admin_features_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.admin_features
    ADD CONSTRAINT admin_features_pkey PRIMARY KEY (id);


--
-- Name: affiliate_codes affiliate_codes_codigo_key; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.affiliate_codes
    ADD CONSTRAINT affiliate_codes_codigo_key UNIQUE (codigo);


--
-- Name: affiliate_codes affiliate_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.affiliate_codes
    ADD CONSTRAINT affiliate_codes_pkey PRIMARY KEY (id);


--
-- Name: affiliate_uses affiliate_uses_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.affiliate_uses
    ADD CONSTRAINT affiliate_uses_pkey PRIMARY KEY (id);


--
-- Name: audit_log audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);


--
-- Name: caja caja_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.caja
    ADD CONSTRAINT caja_pkey PRIMARY KEY (id);


--
-- Name: campanas_marketing campanas_marketing_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.campanas_marketing
    ADD CONSTRAINT campanas_marketing_pkey PRIMARY KEY (id);


--
-- Name: categorias categorias_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.categorias
    ADD CONSTRAINT categorias_pkey PRIMARY KEY (id);


--
-- Name: combo_groups combo_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.combo_groups
    ADD CONSTRAINT combo_groups_pkey PRIMARY KEY (id);


--
-- Name: combo_items combo_items_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.combo_items
    ADD CONSTRAINT combo_items_pkey PRIMARY KEY (id);


--
-- Name: coupons coupons_codigo_key; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.coupons
    ADD CONSTRAINT coupons_codigo_key UNIQUE (codigo);


--
-- Name: coupons coupons_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.coupons
    ADD CONSTRAINT coupons_pkey PRIMARY KEY (id);


--
-- Name: menu_config menu_config_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.menu_config
    ADD CONSTRAINT menu_config_pkey PRIMARY KEY (id);


--
-- Name: notification_outbox notification_outbox_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.notification_outbox
    ADD CONSTRAINT notification_outbox_pkey PRIMARY KEY (id);


--
-- Name: order_events order_events_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.order_events
    ADD CONSTRAINT order_events_pkey PRIMARY KEY (id);


--
-- Name: order_items order_items_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.order_items
    ADD CONSTRAINT order_items_pkey PRIMARY KEY (id);


--
-- Name: order_provider_status order_provider_status_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.order_provider_status
    ADD CONSTRAINT order_provider_status_pkey PRIMARY KEY (id);


--
-- Name: orders orders_numero_pedido_key; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_numero_pedido_key UNIQUE (numero_pedido);


--
-- Name: orders orders_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_pkey PRIMARY KEY (id);


--
-- Name: points_log points_log_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.points_log
    ADD CONSTRAINT points_log_pkey PRIMARY KEY (id);


--
-- Name: price_history price_history_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.price_history
    ADD CONSTRAINT price_history_pkey PRIMARY KEY (id);


--
-- Name: products products_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_pkey PRIMARY KEY (id);


--
-- Name: push_subscriptions push_subscriptions_endpoint_key; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.push_subscriptions
    ADD CONSTRAINT push_subscriptions_endpoint_key UNIQUE (endpoint);


--
-- Name: push_subscriptions push_subscriptions_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.push_subscriptions
    ADD CONSTRAINT push_subscriptions_pkey PRIMARY KEY (id);


--
-- Name: reviews reviews_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.reviews
    ADD CONSTRAINT reviews_pkey PRIMARY KEY (id);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (id);


--
-- Name: site_config site_config_clave_key; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.site_config
    ADD CONSTRAINT site_config_clave_key UNIQUE (clave);


--
-- Name: site_config site_config_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.site_config
    ADD CONSTRAINT site_config_pkey PRIMARY KEY (id);


--
-- Name: staff_payments staff_payments_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.staff_payments
    ADD CONSTRAINT staff_payments_pkey PRIMARY KEY (id);


--
-- Name: stock stock_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.stock
    ADD CONSTRAINT stock_pkey PRIMARY KEY (id);


--
-- Name: admin_features uq_admin_feature; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.admin_features
    ADD CONSTRAINT uq_admin_feature UNIQUE (user_id, feature);


--
-- Name: order_provider_status uq_order_provider_status_order_provider; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.order_provider_status
    ADD CONSTRAINT uq_order_provider_status_order_provider UNIQUE (pedido_id, proveedor_id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: zonas_entrega zonas_entrega_pkey; Type: CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.zonas_entrega
    ADD CONSTRAINT zonas_entrega_pkey PRIMARY KEY (id);


--
-- Name: ix_audit_log_creado_en; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_audit_log_creado_en ON public.audit_log USING btree (creado_en);


--
-- Name: ix_audit_log_user_id; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_audit_log_user_id ON public.audit_log USING btree (user_id);


--
-- Name: ix_caja_fecha; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_caja_fecha ON public.caja USING btree (fecha);


--
-- Name: ix_caja_tipo; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_caja_tipo ON public.caja USING btree (tipo);


--
-- Name: ix_combo_groups_combo_id; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_combo_groups_combo_id ON public.combo_groups USING btree (combo_id);


--
-- Name: ix_combo_groups_combo_orden; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_combo_groups_combo_orden ON public.combo_groups USING btree (combo_id, orden);


--
-- Name: ix_notification_outbox_estado; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_notification_outbox_estado ON public.notification_outbox USING btree (estado);


--
-- Name: ix_notification_outbox_pedido_id; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_notification_outbox_pedido_id ON public.notification_outbox USING btree (pedido_id);


--
-- Name: ix_notification_outbox_siguiente; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_notification_outbox_siguiente ON public.notification_outbox USING btree (siguiente_intento_en);


--
-- Name: ix_order_events_creado_en; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_order_events_creado_en ON public.order_events USING btree (creado_en);


--
-- Name: ix_order_events_pedido_id; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_order_events_pedido_id ON public.order_events USING btree (pedido_id);


--
-- Name: ix_order_events_tipo; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_order_events_tipo ON public.order_events USING btree (tipo);


--
-- Name: ix_order_provider_status_pedido; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_order_provider_status_pedido ON public.order_provider_status USING btree (pedido_id);


--
-- Name: ix_order_provider_status_proveedor; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_order_provider_status_proveedor ON public.order_provider_status USING btree (proveedor_id, preparado);


--
-- Name: ix_orders_cliente_id; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_orders_cliente_id ON public.orders USING btree (cliente_id);


--
-- Name: ix_orders_creado_en; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_orders_creado_en ON public.orders USING btree (creado_en);


--
-- Name: ix_orders_entregado_en; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_orders_entregado_en ON public.orders USING btree (entregado_en);


--
-- Name: ix_orders_estado; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_orders_estado ON public.orders USING btree (estado);


--
-- Name: ix_orders_preparador; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_orders_preparador ON public.orders USING btree (preparador_id);


--
-- Name: ix_orders_repartidor; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_orders_repartidor ON public.orders USING btree (repartidor_id);


--
-- Name: ix_points_log_cliente_id; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_points_log_cliente_id ON public.points_log USING btree (cliente_id);


--
-- Name: ix_points_log_creado_en; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_points_log_creado_en ON public.points_log USING btree (creado_en);


--
-- Name: ix_push_sub_rol; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_push_sub_rol ON public.push_subscriptions USING btree (rol);


--
-- Name: ix_push_sub_user_id; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_push_sub_user_id ON public.push_subscriptions USING btree (user_id);


--
-- Name: ix_stock_caducidad; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_stock_caducidad ON public.stock USING btree (fecha_caducidad);


--
-- Name: ix_stock_producto_id; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE INDEX ix_stock_producto_id ON public.stock USING btree (producto_id);


--
-- Name: uq_affiliate_use_order; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE UNIQUE INDEX uq_affiliate_use_order ON public.affiliate_uses USING btree (codigo_id, pedido_id);


--
-- Name: uq_caja_order_income; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE UNIQUE INDEX uq_caja_order_income ON public.caja USING btree (pedido_id) WHERE (((tipo)::text = 'ingreso'::text) AND (pedido_id IS NOT NULL));


--
-- Name: uq_caja_order_refund; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE UNIQUE INDEX uq_caja_order_refund ON public.caja USING btree (pedido_id) WHERE (((tipo)::text = 'egreso'::text) AND ((categoria)::text = 'devolucion'::text) AND (pedido_id IS NOT NULL));


--
-- Name: uq_caja_staff_payment_expense; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE UNIQUE INDEX uq_caja_staff_payment_expense ON public.caja USING btree (staff_payment_id) WHERE (((tipo)::text = 'egreso'::text) AND (staff_payment_id IS NOT NULL));


--
-- Name: uq_points_log_order_earned; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE UNIQUE INDEX uq_points_log_order_earned ON public.points_log USING btree (cliente_id, pedido_id, tipo) WHERE (((tipo)::text = 'ganado'::text) AND (pedido_id IS NOT NULL));


--
-- Name: uq_staff_payment_delivery_commission; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE UNIQUE INDEX uq_staff_payment_delivery_commission ON public.staff_payments USING btree (user_id, origen, pedido_id) WHERE (((tipo)::text = 'comision'::text) AND (pedido_id IS NOT NULL));


--
-- Name: uq_users_telefono_normalizado; Type: INDEX; Schema: public; Owner: oxidian
--

CREATE UNIQUE INDEX uq_users_telefono_normalizado ON public.users USING btree (telefono_normalizado) WHERE (telefono_normalizado IS NOT NULL);


--
-- Name: admin_features admin_features_actualizado_por_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.admin_features
    ADD CONSTRAINT admin_features_actualizado_por_fkey FOREIGN KEY (actualizado_por) REFERENCES public.users(id);


--
-- Name: admin_features admin_features_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.admin_features
    ADD CONSTRAINT admin_features_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: affiliate_codes affiliate_codes_creado_por_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.affiliate_codes
    ADD CONSTRAINT affiliate_codes_creado_por_fkey FOREIGN KEY (creado_por) REFERENCES public.users(id);


--
-- Name: affiliate_codes affiliate_codes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.affiliate_codes
    ADD CONSTRAINT affiliate_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: affiliate_uses affiliate_uses_cliente_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.affiliate_uses
    ADD CONSTRAINT affiliate_uses_cliente_id_fkey FOREIGN KEY (cliente_id) REFERENCES public.users(id);


--
-- Name: affiliate_uses affiliate_uses_codigo_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.affiliate_uses
    ADD CONSTRAINT affiliate_uses_codigo_id_fkey FOREIGN KEY (codigo_id) REFERENCES public.affiliate_codes(id);


--
-- Name: affiliate_uses affiliate_uses_pedido_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.affiliate_uses
    ADD CONSTRAINT affiliate_uses_pedido_id_fkey FOREIGN KEY (pedido_id) REFERENCES public.orders(id);


--
-- Name: affiliate_uses affiliate_uses_staff_payment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.affiliate_uses
    ADD CONSTRAINT affiliate_uses_staff_payment_id_fkey FOREIGN KEY (staff_payment_id) REFERENCES public.staff_payments(id);


--
-- Name: audit_log audit_log_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: caja caja_pedido_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.caja
    ADD CONSTRAINT caja_pedido_id_fkey FOREIGN KEY (pedido_id) REFERENCES public.orders(id);


--
-- Name: caja caja_registrado_por_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.caja
    ADD CONSTRAINT caja_registrado_por_fkey FOREIGN KEY (registrado_por) REFERENCES public.users(id);


--
-- Name: caja caja_staff_payment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.caja
    ADD CONSTRAINT caja_staff_payment_id_fkey FOREIGN KEY (staff_payment_id) REFERENCES public.staff_payments(id);


--
-- Name: campanas_marketing campanas_marketing_creado_por_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.campanas_marketing
    ADD CONSTRAINT campanas_marketing_creado_por_fkey FOREIGN KEY (creado_por) REFERENCES public.users(id);


--
-- Name: campanas_marketing campanas_marketing_zona_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.campanas_marketing
    ADD CONSTRAINT campanas_marketing_zona_id_fkey FOREIGN KEY (zona_id) REFERENCES public.zonas_entrega(id);


--
-- Name: combo_groups combo_groups_combo_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.combo_groups
    ADD CONSTRAINT combo_groups_combo_id_fkey FOREIGN KEY (combo_id) REFERENCES public.products(id) ON DELETE CASCADE;


--
-- Name: combo_items combo_items_combo_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.combo_items
    ADD CONSTRAINT combo_items_combo_id_fkey FOREIGN KEY (combo_id) REFERENCES public.products(id);


--
-- Name: combo_items combo_items_producto_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.combo_items
    ADD CONSTRAINT combo_items_producto_id_fkey FOREIGN KEY (producto_id) REFERENCES public.products(id);


--
-- Name: menu_config menu_config_categoria_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.menu_config
    ADD CONSTRAINT menu_config_categoria_id_fkey FOREIGN KEY (categoria_id) REFERENCES public.categorias(id);


--
-- Name: menu_config menu_config_creado_por_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.menu_config
    ADD CONSTRAINT menu_config_creado_por_fkey FOREIGN KEY (creado_por) REFERENCES public.users(id);


--
-- Name: menu_config menu_config_producto_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.menu_config
    ADD CONSTRAINT menu_config_producto_id_fkey FOREIGN KEY (producto_id) REFERENCES public.products(id);


--
-- Name: notification_outbox notification_outbox_pedido_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.notification_outbox
    ADD CONSTRAINT notification_outbox_pedido_id_fkey FOREIGN KEY (pedido_id) REFERENCES public.orders(id);


--
-- Name: notification_outbox notification_outbox_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.notification_outbox
    ADD CONSTRAINT notification_outbox_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: order_events order_events_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.order_events
    ADD CONSTRAINT order_events_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.users(id);


--
-- Name: order_events order_events_pedido_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.order_events
    ADD CONSTRAINT order_events_pedido_id_fkey FOREIGN KEY (pedido_id) REFERENCES public.orders(id);


--
-- Name: order_items order_items_pedido_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.order_items
    ADD CONSTRAINT order_items_pedido_id_fkey FOREIGN KEY (pedido_id) REFERENCES public.orders(id);


--
-- Name: order_items order_items_producto_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.order_items
    ADD CONSTRAINT order_items_producto_id_fkey FOREIGN KEY (producto_id) REFERENCES public.products(id);


--
-- Name: order_provider_status order_provider_status_actualizado_por_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.order_provider_status
    ADD CONSTRAINT order_provider_status_actualizado_por_fkey FOREIGN KEY (actualizado_por) REFERENCES public.users(id);


--
-- Name: order_provider_status order_provider_status_pedido_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.order_provider_status
    ADD CONSTRAINT order_provider_status_pedido_id_fkey FOREIGN KEY (pedido_id) REFERENCES public.orders(id) ON DELETE CASCADE;


--
-- Name: order_provider_status order_provider_status_proveedor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.order_provider_status
    ADD CONSTRAINT order_provider_status_proveedor_id_fkey FOREIGN KEY (proveedor_id) REFERENCES public.users(id);


--
-- Name: orders orders_afiliado_codigo_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_afiliado_codigo_id_fkey FOREIGN KEY (afiliado_codigo_id) REFERENCES public.affiliate_codes(id);


--
-- Name: orders orders_cajero_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_cajero_id_fkey FOREIGN KEY (cajero_id) REFERENCES public.users(id);


--
-- Name: orders orders_cliente_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_cliente_id_fkey FOREIGN KEY (cliente_id) REFERENCES public.users(id);


--
-- Name: orders orders_cupon_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_cupon_id_fkey FOREIGN KEY (cupon_id) REFERENCES public.coupons(id);


--
-- Name: orders orders_pago_confirmado_por_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_pago_confirmado_por_fkey FOREIGN KEY (pago_confirmado_por) REFERENCES public.users(id);


--
-- Name: orders orders_preparador_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_preparador_id_fkey FOREIGN KEY (preparador_id) REFERENCES public.users(id);


--
-- Name: orders orders_repartidor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_repartidor_id_fkey FOREIGN KEY (repartidor_id) REFERENCES public.users(id);


--
-- Name: orders orders_zona_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_zona_id_fkey FOREIGN KEY (zona_id) REFERENCES public.zonas_entrega(id);


--
-- Name: points_log points_log_cliente_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.points_log
    ADD CONSTRAINT points_log_cliente_id_fkey FOREIGN KEY (cliente_id) REFERENCES public.users(id);


--
-- Name: points_log points_log_pedido_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.points_log
    ADD CONSTRAINT points_log_pedido_id_fkey FOREIGN KEY (pedido_id) REFERENCES public.orders(id);


--
-- Name: price_history price_history_cambiado_por_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.price_history
    ADD CONSTRAINT price_history_cambiado_por_fkey FOREIGN KEY (cambiado_por) REFERENCES public.users(id);


--
-- Name: price_history price_history_producto_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.price_history
    ADD CONSTRAINT price_history_producto_id_fkey FOREIGN KEY (producto_id) REFERENCES public.products(id);


--
-- Name: products products_categoria_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_categoria_id_fkey FOREIGN KEY (categoria_id) REFERENCES public.categorias(id);


--
-- Name: products products_proveedor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_proveedor_id_fkey FOREIGN KEY (proveedor_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: push_subscriptions push_subscriptions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.push_subscriptions
    ADD CONSTRAINT push_subscriptions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: reviews reviews_cliente_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.reviews
    ADD CONSTRAINT reviews_cliente_id_fkey FOREIGN KEY (cliente_id) REFERENCES public.users(id);


--
-- Name: reviews reviews_pedido_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.reviews
    ADD CONSTRAINT reviews_pedido_id_fkey FOREIGN KEY (pedido_id) REFERENCES public.orders(id);


--
-- Name: reviews reviews_producto_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.reviews
    ADD CONSTRAINT reviews_producto_id_fkey FOREIGN KEY (producto_id) REFERENCES public.products(id);


--
-- Name: site_config site_config_actualizado_por_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.site_config
    ADD CONSTRAINT site_config_actualizado_por_fkey FOREIGN KEY (actualizado_por) REFERENCES public.users(id);


--
-- Name: staff_payments staff_payments_pedido_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.staff_payments
    ADD CONSTRAINT staff_payments_pedido_id_fkey FOREIGN KEY (pedido_id) REFERENCES public.orders(id);


--
-- Name: staff_payments staff_payments_registrado_por_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.staff_payments
    ADD CONSTRAINT staff_payments_registrado_por_fkey FOREIGN KEY (registrado_por) REFERENCES public.users(id);


--
-- Name: staff_payments staff_payments_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.staff_payments
    ADD CONSTRAINT staff_payments_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: stock stock_producto_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: oxidian
--

ALTER TABLE ONLY public.stock
    ADD CONSTRAINT stock_producto_id_fkey FOREIGN KEY (producto_id) REFERENCES public.products(id);


--
-- PostgreSQL database dump complete
--

\unrestrict peGD4cWdTsoJAysd4U2scI3yDVkhkzj7zuidAcmoPO3crezGpb1eJaj2sP7Btxi

