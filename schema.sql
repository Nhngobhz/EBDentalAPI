-- Reference schema only, generated via `pg_dump --schema-only` after running the
-- Alembic migrations in this project. The migrations (alembic/versions/) are the
-- actual source of truth - this file is here so you can see the final DDL at a glance.

--
-- PostgreSQL database dump
--


-- Dumped from database version 16.14 (Ubuntu 16.14-0ubuntu0.24.04.1)
-- Dumped by pg_dump version 16.14 (Ubuntu 16.14-0ubuntu0.24.04.1)

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
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: brands; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.brands (
    id integer NOT NULL,
    brand_name character varying(150) NOT NULL,
    brand_image character varying(500),
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: brands_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.brands_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: brands_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.brands_id_seq OWNED BY public.brands.id;


--
-- Name: customers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customers (
    id integer NOT NULL,
    customer_name character varying(150) NOT NULL,
    email character varying(255) NOT NULL,
    address character varying(255),
    phone_num character varying(30),
    customer_image character varying(500),
    access_permission boolean NOT NULL,
    creation_date timestamp with time zone DEFAULT now(),
    hashed_password character varying(255),
    is_active boolean NOT NULL,
    is_verified boolean NOT NULL,
    verification_token character varying(255),
    verification_token_expires timestamp with time zone,
    reset_token character varying(255),
    reset_token_expires timestamp with time zone,
    last_login timestamp with time zone
);


--
-- Name: customers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.customers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: customers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.customers_id_seq OWNED BY public.customers.id;


--
-- Name: manuals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.manuals (
    id integer NOT NULL,
    product_id integer NOT NULL,
    description text,
    manual_image character varying(500),
    pdf character varying(500),
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: manuals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.manuals_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: manuals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.manuals_id_seq OWNED BY public.manuals.id;


--
-- Name: products; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.products (
    id integer NOT NULL,
    product_name character varying(200) NOT NULL,
    description text,
    old_price numeric(10,2),
    price numeric(10,2) NOT NULL,
    brand_id integer NOT NULL,
    category character varying(100),
    badge character varying(50),
    product_image character varying(500),
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: products_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.products_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: products_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.products_id_seq OWNED BY public.products.id;


--
-- Name: promotions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.promotions (
    id integer NOT NULL,
    promotion_name character varying(200) NOT NULL,
    description text,
    price numeric(10,2) NOT NULL,
    old_price numeric(10,2),
    start_date timestamp with time zone NOT NULL,
    end_date timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: promotions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.promotions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: promotions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.promotions_id_seq OWNED BY public.promotions.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id integer NOT NULL,
    user_name character varying(100) NOT NULL,
    email character varying(255) NOT NULL,
    address character varying(255),
    phone_num character varying(30),
    user_image character varying(500),
    role_title character varying(100) NOT NULL,
    creation_date timestamp with time zone DEFAULT now(),
    user_management boolean NOT NULL,
    price_listing boolean NOT NULL,
    product_management boolean NOT NULL,
    customer_management boolean NOT NULL,
    hashed_password character varying(255) NOT NULL,
    is_active boolean NOT NULL,
    is_verified boolean NOT NULL,
    verification_token character varying(255),
    verification_token_expires timestamp with time zone,
    reset_token character varying(255),
    reset_token_expires timestamp with time zone,
    last_login timestamp with time zone
);


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: brands id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.brands ALTER COLUMN id SET DEFAULT nextval('public.brands_id_seq'::regclass);


--
-- Name: customers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customers ALTER COLUMN id SET DEFAULT nextval('public.customers_id_seq'::regclass);


--
-- Name: manuals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manuals ALTER COLUMN id SET DEFAULT nextval('public.manuals_id_seq'::regclass);


--
-- Name: products id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.products ALTER COLUMN id SET DEFAULT nextval('public.products_id_seq'::regclass);


--
-- Name: promotions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.promotions ALTER COLUMN id SET DEFAULT nextval('public.promotions_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: brands brands_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.brands
    ADD CONSTRAINT brands_pkey PRIMARY KEY (id);


--
-- Name: customers customers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customers
    ADD CONSTRAINT customers_pkey PRIMARY KEY (id);


--
-- Name: manuals manuals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manuals
    ADD CONSTRAINT manuals_pkey PRIMARY KEY (id);


--
-- Name: products products_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_pkey PRIMARY KEY (id);


--
-- Name: promotions promotions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.promotions
    ADD CONSTRAINT promotions_pkey PRIMARY KEY (id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: ix_brands_brand_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_brands_brand_name ON public.brands USING btree (brand_name);


--
-- Name: ix_brands_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_brands_id ON public.brands USING btree (id);


--
-- Name: ix_customers_customer_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_customers_customer_name ON public.customers USING btree (customer_name);


--
-- Name: ix_customers_email; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_customers_email ON public.customers USING btree (email);


--
-- Name: ix_customers_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_customers_id ON public.customers USING btree (id);


--
-- Name: ix_customers_reset_token; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_customers_reset_token ON public.customers USING btree (reset_token);


--
-- Name: ix_customers_verification_token; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_customers_verification_token ON public.customers USING btree (verification_token);


--
-- Name: ix_manuals_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_manuals_id ON public.manuals USING btree (id);


--
-- Name: ix_products_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_products_category ON public.products USING btree (category);


--
-- Name: ix_products_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_products_id ON public.products USING btree (id);


--
-- Name: ix_products_product_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_products_product_name ON public.products USING btree (product_name);


--
-- Name: ix_promotions_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_promotions_id ON public.promotions USING btree (id);


--
-- Name: ix_promotions_promotion_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_promotions_promotion_name ON public.promotions USING btree (promotion_name);


--
-- Name: ix_users_email; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_users_email ON public.users USING btree (email);


--
-- Name: ix_users_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_id ON public.users USING btree (id);


--
-- Name: ix_users_reset_token; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_users_reset_token ON public.users USING btree (reset_token);


--
-- Name: ix_users_verification_token; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_users_verification_token ON public.users USING btree (verification_token);


--
-- Name: manuals manuals_product_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manuals
    ADD CONSTRAINT manuals_product_id_fkey FOREIGN KEY (product_id) REFERENCES public.products(id) ON DELETE CASCADE;


--
-- Name: products products_brand_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_brand_id_fkey FOREIGN KEY (brand_id) REFERENCES public.brands(id) ON DELETE RESTRICT;


--
-- PostgreSQL database dump complete
--


