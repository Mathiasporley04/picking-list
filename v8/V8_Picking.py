#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extractor Avanzado de Productos Mercado Libre Uruguay - CON FILTROS Y LÓGICA TEMPORAL
------------------------------------------------------------------------------------
Genera JSON + PDF desde un HTML guardado del panel de ventas/pedidos de Mercado Libre.
NUEVA FUNCIONALIDAD: Separa productos "a acordar" por fecha (antes/después de ayer 16:00)
FILTROS: Elimina productos con estados específicos como "reprogramado por el comprador"
DESCARGA DE IMÁGENES ARREGLADA: Múltiples estrategias para descargar imágenes exitosamente

Requisitos:
    pip install beautifulsoup4 reportlab requests pillow
"""

import json
import re
import os
import sys
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import threading
import tempfile
import time
from urllib.parse import urlparse, parse_qs
from reportlab.lib.units import inch
from typing import Dict, List, Optional, Any, Tuple



# GUI
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# HTML
from bs4 import BeautifulSoup

# PDF
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph,
    Spacer, PageBreak, Image as RLImage
)
from reportlab.lib.enums import TA_CENTER

# HTTP
import requests

# AUTOMATIZACIÓN CON TECLAS
import pyautogui
import threading
import time
import keyboard

# -------- LOGGING -------- #
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('ml_extractor.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

_IMAGE_CACHE = {}  # cache imágenes


# ===================== EXTRACTOR ===================== #
class MLProductExtractor:
    def __init__(self):
        self.products = []
        self.filtered_products = []  # productos filtrados
        self.urgent_products = []    # NUEVO: productos urgentes (a acordar después de ayer 16:00)
        self.to_review_products = [] # NUEVO: productos a revisar (a acordar antes de ayer 16:00)
        self.debug_report = []
        
        # NUEVO: Calcular umbral temporal (ayer a las 16:00)
        self.temporal_threshold = self._calculate_temporal_threshold()
        
        self.stats = {
            'total_found': 0,
            'successfully_extracted': 0,
            'filtered_out': 0,
            'urgent_count': 0,        # NUEVO: contador de urgentes
            'to_review_count': 0,     # NUEVO: contador de "a revisar"
            'normal_count': 0,        # NUEVO: contador de productos normales
            'final_count': 0,
            'filter_reasons': {},
            'fields_completeness': {
                'nombre': 0, 'link': 0, 'imagen': 0,
                'precio': 0, 'cantidad': 0, 'sku': 0
            }
        }
        self.base_dir = None
        
        # Estados a filtrar (puedes agregar más aquí)
        self.filter_states = [
            # Reprogramados - términos más generales para capturar todas las variantes
            'reprogramado',
            'envío reprogramado',
            'reprogramado por el comprador',
            'envío reprogramado por el comprador',
            'cancelado',
            'cancelada',  # NUEVO: Mercado Libre usa "Cancelada" (femenino)
            'devuelto',
            'reembolsado',
            'está demorado',
            'demorado',
            # CORREGIDO: Solo estados de productos realmente entregados
            'comprador ausente',
            'entregado al conductor',
            'entregado',
            'fue entregado',
            'ya fue entregado',
            'producto entregado',
            'pedido entregado',
            'envío entregado',
            'entrega completada',
            'entrega finalizada',
        ]
        
        # NUEVO: Estados que requieren análisis temporal ("a acordar")
        self.temporal_states = [
            'acuerdas la entrega',
            'acuerda la entrega',
            'acuerdo la entrega',
            'a acordar con el comprador',
            'contactate con tu comprador',
            'avisar entrega'
        ]
        
        # Lista de productos conocidos que fueron reprogramados (por SKU o nombre)
        self.known_reprogrammed_products = [
            'STOCK A2-9',
            'Balanza Digital Joyería, Balanza Precisión, Báscula Joyería'
        ]
        
        # Lista de productos conocidos que han estado demorados
        self.known_delayed_products = [
            'SKU233',
            'Pulsera Con Imanes Unisex Terap. Adelgaza- Artritis Y Stress Plateado 0 Mm'
        ]

    def _calculate_temporal_threshold(self):
        """
        Calcula el umbral temporal considerando fines de semana:
        - Lunes: Viernes anterior a las 16:00 (porque no se trabaja sábado/domingo)
        - Martes a Viernes: Día anterior a las 16:00
        - Sábado/Domingo: Viernes anterior a las 16:00 (por si se ejecuta en fin de semana)
        """
        now = datetime.now()
        current_weekday = now.weekday()  # 0=Lunes, 1=Martes, ..., 6=Domingo
        
        if current_weekday == 0:  # Lunes
            # Umbral = Viernes anterior a las 16:00 (3 días atrás)
            threshold_date = now - timedelta(days=3)
            threshold = threshold_date.replace(hour=16, minute=0, second=0, microsecond=0)
            logger.info(f"🗓️  Es LUNES - Umbral especial: Viernes {threshold.strftime('%d/%m/%Y %H:%M')}")
            
        elif current_weekday in [5, 6]:  # Sábado (5) o Domingo (6)
            # Si por alguna razón se ejecuta en fin de semana, usar viernes anterior
            days_to_friday = current_weekday - 4  # 5-4=1 (sábado), 6-4=2 (domingo)
            threshold_date = now - timedelta(days=days_to_friday)
            threshold = threshold_date.replace(hour=16, minute=0, second=0, microsecond=0)
            logger.info(f"🗓️  Es FIN DE SEMANA - Umbral: Viernes {threshold.strftime('%d/%m/%Y %H:%M')}")
            
        else:  # Martes (1), Miércoles (2), Jueves (3), Viernes (4)
            # Lógica normal: día anterior a las 16:00
            yesterday = now - timedelta(days=1)
            threshold = yesterday.replace(hour=16, minute=0, second=0, microsecond=0)
            logger.info(f"🗓️  Es día laboral - Umbral normal: {threshold.strftime('%d/%m/%Y %H:%M')}")
        
        logger.info(f"🕐 Umbral temporal calculado: {threshold.strftime('%d/%m/%Y %H:%M')}")
        logger.info(f"   Pedidos 'a acordar' después de esta fecha serán URGENTES")
        logger.info(f"   Pedidos 'a acordar' antes de esta fecha irán a 'RETIROS A REVISAR'")
        
        return threshold

    def detect_encoding(self, file_path):
        for enc in ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1']:
            try:
                file_path.read_text(encoding=enc)
                logger.info(f"Encoding detectado: {enc}")
                return enc
            except UnicodeDecodeError:
                pass
        logger.warning("No se detectó encoding. Uso utf-8 errors='ignore'.")
        return 'utf-8'

    def load_html(self, file_path):
        self.base_dir = file_path.parent
        enc = self.detect_encoding(file_path)
        html = file_path.read_text(encoding=enc, errors='ignore')
        logger.info(f"Archivo cargado: {file_path.name} ({len(html)} bytes)")
        return BeautifulSoup(html, 'html.parser')

    def find_element_flexible(self, parent, strategies):
        for strat in strategies:
            try:
                t = strat['type']
                if t == 'class':
                    el = parent.find(class_=strat['value'])
                    if el:
                        return el
                    pat = re.compile(rf".*\b{re.escape(strat['value'])}\b.*")
                    el = parent.find(class_=pat)
                    if el:
                        return el
                elif t == 'css':
                    el = parent.select_one(strat['value'])
                    if el:
                        return el
                elif t == 'tag_text':
                    for el in parent.find_all(strat['tag']):
                        if strat['text_pattern'].lower() in el.get_text(strip=True).lower():
                            return el
                elif t == 'custom':
                    el = strat['function'](parent)
                    if el:
                        return el
            except Exception as e:
                logger.debug(f"Estrategia falló {strat}: {e}")
        return None

    def extract_order_status(self, container):
        """
        Extrae el estado del pedido desde diferentes ubicaciones posibles en el HTML
        """
        # ESTRATEGIA 1: Buscar en elementos HTML visibles
        status_strategies = [
            {'type': 'css', 'value': '.sc-status-action-row__status'},
            {'type': 'css', 'value': 'span.sc-status-action-row__status'},
            {'type': 'class', 'value': 'sc-status-action-row__status'},
            {'type': 'css', 'value': '.sc-status-action-row-status'},
            {'type': 'css', 'value': 'span[class*="status"]'},
            {'type': 'custom', 'function': lambda p: p.find('span', text=re.compile(r'reprogramado|cancelado|devuelto|reembolsado|demorado|acuerdas|acuerda|acordar', re.I))},
        ]
        
        status_el = self.find_element_flexible(container, status_strategies)
        if status_el:
            status_text = status_el.get_text(strip=True).lower()
            logger.debug(f"Estado encontrado en HTML: {status_text}")
            return status_text
        
        # ESTRATEGIA 2: Buscar en JSON embebido dentro de scripts
        scripts = container.find_all('script')
        for script in scripts:
            if script.string:
                script_content = script.string
                status_match = re.search(r'"status"\s*:\s*"([^"]*)"', script_content, re.I)
                if status_match:
                    status_from_json = status_match.group(1).lower()
                    logger.debug(f"Estado encontrado en JSON: {status_from_json}")
                    # Verificar si contiene palabras clave relevantes (mejorado)
                    all_keywords = self.filter_states + self.temporal_states
                    for keyword in all_keywords:
                        if keyword.lower() in status_from_json:
                            return status_from_json
                    
                    # NUEVO: Verificaciones adicionales para estados ML
                    if any(word in status_from_json for word in ['cancelad', 'devuelt', 'reembolsad']):
                        return status_from_json
        
        # ESTRATEGIA 3: Buscar en todo el texto del contenedor
        all_text = container.get_text(separator=' ', strip=True).lower()
        
        # Buscar patrones específicos de estados temporales PRIMERO
        temporal_patterns = [
            r'acuerdas?\s+la\s+entrega',
            r'acuerdo\s+la\s+entrega',
            r'a\s+acordar\s+con\s+el\s+comprador',
            r'contacta[rt]?e?\s+con\s+tu\s+comprador',
            r'avisar\s+entrega'
        ]
        
        for pattern in temporal_patterns:
            if re.search(pattern, all_text, re.I):
                found_text = re.search(pattern, all_text, re.I).group(0)
                logger.debug(f"Estado temporal encontrado por patrón: {found_text}")
                return found_text
        
        # Luego buscar otros estados de filtro
        for filter_state in self.filter_states:
            if filter_state.lower() in all_text:
                logger.debug(f"Estado encontrado en texto completo: {filter_state}")
                return filter_state.lower()
        
        # ESTRATEGIA 4: Buscar patrones de JSON en todo el HTML como texto
        container_html = str(container)
        json_patterns = [
            r'"status"\s*:\s*"([^"]*acuerd[^"]*)"',
            r'"status"\s*:\s*"([^"]*acordar[^"]*)"',
            r'"status"\s*:\s*"([^"]*reprogramado[^"]*)"',
            r'"status"\s*:\s*"([^"]*cancelad[^"]*)"',  # MEJORADO: Captura "cancelado" y "cancelada"
            r'"status"\s*:\s*"([^"]*devuelt[^"]*)"',   # MEJORADO: Captura "devuelto" y "devuelta"
            r'"status"\s*:\s*"([^"]*reembolsad[^"]*)"', # MEJORADO: Captura "reembolsado" y "reembolsada"
            r'"status"\s*:\s*"([^"]*demorado[^"]*)"'
        ]
        
        for pattern in json_patterns:
            match = re.search(pattern, container_html, re.I)
            if match:
                status_from_pattern = match.group(1).lower()
                logger.debug(f"Estado encontrado por patrón JSON: {status_from_pattern}")
                return status_from_pattern
        
        return ""

    def _extract_order_date(self, container):
        """
        Extrae la fecha del pedido y la convierte a datetime
        VERSIÓN MEJORADA: Maneja diferentes formatos de fecha de MercadoLibre
        """
        # Buscar elementos de fecha con diferentes estrategias
        date_strategies = [
            {'type': 'css', 'value': '.pack-status-info__date'},
            {'type': 'css', 'value': '.ui-pack-status-date'},
            {'type': 'css', 'value': '[class*="date"]'},
            {'type': 'css', 'value': '[class*="fecha"]'},
            {'type': 'custom', 'function': lambda p: p.find(text=re.compile(r'\d{1,2}\s+(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)', re.I))},
        ]
        
        date_element = self.find_element_flexible(container, date_strategies)
        if date_element:
            date_text = date_element.get_text(strip=True) if hasattr(date_element, 'get_text') else str(date_element)
        else:
            # Buscar en todo el texto del contenedor
            all_text = container.get_text(separator=' ', strip=True)
            date_match = re.search(r'(\d{1,2}\s+(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)(?:\s+\d{4})?(?:\s+\d{1,2}:\d{2})?)', all_text, re.I)
            if date_match:
                date_text = date_match.group(1)
            else:
                return None
        
        # Convertir texto de fecha a datetime
        return self._parse_ml_date(date_text)

    def _parse_ml_date(self, date_text):
        """
        Convierte texto de fecha de MercadoLibre a datetime
        Ejemplos: "21 jul", "21 jul 2024", "21 jul 14:30", "21 jul 2024 14:30"
        """
        if not date_text:
            return None
            
        try:
            # Limpiar el texto
            date_text = date_text.strip().lower()
            
            # Mapeo de meses en español
            month_mapping = {
                'ene': 1, 'feb': 2, 'mar': 3, 'abr': 4, 'may': 5, 'jun': 6,
                'jul': 7, 'ago': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dic': 12
            }
            
            # Extraer componentes usando regex
            # Patrón más completo: día + mes + (año opcional) + (hora opcional)
            pattern = r'(\d{1,2})\s+(\w{3})(?:\s+(\d{4}))?(?:\s+(\d{1,2}):(\d{2}))?'
            match = re.search(pattern, date_text, re.I)
            
            if not match:
                logger.warning(f"No se pudo parsear fecha: {date_text}")
                return None
            
            day = int(match.group(1))
            month_text = match.group(2).lower()
            year = int(match.group(3)) if match.group(3) else datetime.now().year
            hour = int(match.group(4)) if match.group(4) else 0
            minute = int(match.group(5)) if match.group(5) else 0
            
            if month_text not in month_mapping:
                logger.warning(f"Mes no reconocido: {month_text}")
                return None
                
            month = month_mapping[month_text]
            
            parsed_date = datetime(year, month, day, hour, minute)
            logger.debug(f"Fecha parseada: '{date_text}' -> {parsed_date}")
            return parsed_date
            
        except Exception as e:
            logger.warning(f"Error parseando fecha '{date_text}': {e}")
            return None

    def _classify_temporal_product(self, status, order_date):
        """
        Clasifica un producto con estado temporal basado en la fecha
        Retorna: 'urgent', 'to_review', o None
        """
        if not status:
            return None
            
        # Verificar si el estado es uno de los temporales
        is_temporal = False
        for temporal_state in self.temporal_states:
            if temporal_state.lower() in status.lower():
                is_temporal = True
                break
                
        if not is_temporal:
            return None
            
        # Si no tenemos fecha, asumir que es urgente (mejor ser conservador)
        if not order_date:
            logger.warning(f"Producto temporal sin fecha detectada, asumiendo URGENTE: {status}")
            return 'urgent'
            
        # Comparar con el umbral
        if order_date > self.temporal_threshold:
            logger.debug(f"Producto temporal URGENTE: {order_date} > {self.temporal_threshold}")
            return 'urgent'
        else:
            logger.debug(f"Producto temporal A REVISAR: {order_date} <= {self.temporal_threshold}")
            return 'to_review'

    def should_filter_product(self, container, product_data):
        """
        Determina si un producto debe ser filtrado basado en su estado o historial conocido
        Retorna (debe_filtrar, razon)
        """
        # ESTRATEGIA 1: Verificar estado actual (solo estados de filtro, no temporales)
        status = self.extract_order_status(container)
        
        if status:
            # Verificar si el estado actual coincide con alguno de los filtros (NO temporales)
            # MEJORADO: Búsqueda más flexible para capturar variaciones
            status_lower = status.lower()
            for filter_state in self.filter_states:
                filter_lower = filter_state.lower()
                if filter_lower in status_lower:
                    return True, f"estado actual: {filter_state}"
            
            # NUEVO: Verificaciones adicionales para estados específicos de ML
            if 'cancelad' in status_lower:  # Captura "cancelado", "cancelada", etc.
                return True, f"estado actual: cancelada (ML)"
            if 'cancelaste' in status_lower:  # Captura "cancelaste la venta"
                return True, f"estado actual: cancelaste la venta (ML)"
            if 'comprador cancel' in status_lower:  # Captura "cancelada por el comprador"
                return True, f"estado actual: cancelada por comprador (ML)"
            if 'devuelt' in status_lower:   # Captura "devuelto", "devuelta", etc.
                return True, f"estado actual: devuelto (ML)"
            if 'reembolsad' in status_lower:  # Captura "reembolsado", "reembolsada", etc.
                return True, f"estado actual: reembolsado (ML)"
            if 'reclam' in status_lower:  # Captura "reclamo", "reclamos", etc.
                return True, f"estado actual: reclamo (ML)"
            if 'mediaci' in status_lower:  # Captura "mediación", "mediacion", etc.
                return True, f"estado actual: mediación (ML)"
            # CORREGIDO: Solo filtrar productos realmente entregados, no los que están en camino
            # Los productos "en camino", "en tránsito", "enviado" NO son entregados
            # Solo filtrar si realmente dice que fue entregado
            if any(entregado_term in status_lower for entregado_term in [
                'entregado al conductor',
                'entregado',
                'fue entregado', 
                'ya fue entregado',
                'producto entregado',
                'pedido entregado',
                'envío entregado',
                'entrega completada',
                'entrega finalizada'
            ]):
                return True, f"estado actual: entregado (ML)"
            
            # Filtrar otros problemas específicos
            if 'problema' in status_lower or 'pendiente' in status_lower:
                return True, f"estado actual: problema/pendiente (ML)"
        
        # ESTRATEGIA 2: Verificar productos conocidos como reprogramados (por SKU)
        product_sku = product_data.get('sku', '').strip()
        if product_sku and product_sku in self.known_reprogrammed_products:
            return True, f"producto conocido reprogramado (SKU: {product_sku})"
        
        # ESTRATEGIA 3: Verificar productos conocidos como reprogramados (por nombre)
        product_name = product_data.get('nombre', '').strip()
        if product_name and product_name in self.known_reprogrammed_products:
            return True, f"producto conocido reprogramado (nombre: {product_name[:30]}...)"
        
        # ESTRATEGIA 4: Verificar productos conocidos como demorados (por SKU)
        if product_sku and product_sku in self.known_delayed_products:
            return True, f"producto conocido demorado (SKU: {product_sku})"
        
        # ESTRATEGIA 5: Verificar productos conocidos como demorados (por nombre)
        if product_name and product_name in self.known_delayed_products:
            return True, f"producto conocido demorado (nombre: {product_name[:30]}...)"
        
        # ESTRATEGIA 6: Buscar patrones en la URL o ID del pedido que indiquen problemas
        order_id = self._extract_order_id(container)
        if order_id:
            known_problematic_orders = [
                '2000008407186271',
                '2000012378209506'   
            ]
            if order_id in known_problematic_orders:
                return True, f"pedido conocido problemático (ID: {order_id})"
        
        return False, ""

    def _extract_order_id(self, container):
        """Extrae el ID del pedido del contenedor"""
        id_patterns = [
            r'#(\d+)',
            r'pack_id["\']?\s*:\s*["\']?(\d+)',
            r'order[_-]?id["\']?\s*:\s*["\']?(\d+)',
        ]
        
        container_text = str(container)
        for pattern in id_patterns:
            match = re.search(pattern, container_text, re.I)
            if match:
                return match.group(1)
        
        id_elements = container.find_all(class_=re.compile('pack.id|order.id|left-column__pack-id'))
        for element in id_elements:
            text = element.get_text(strip=True)
            match = re.search(r'(\d{10,})', text)
            if match:
                return match.group(1)
        
        return None

    def _get_longest_text(self, parent):
        """Helper para encontrar el div con más texto"""
        divs = parent.find_all('div')
        if not divs:
            return None
        
        max_text = ""
        max_div = None
        for div in divs:
            text = div.get_text(strip=True)
            if len(text) > len(max_text):
                max_text = text
                max_div = div
        
        return max_div

    def extract_product_name(self, container):
        product_row = container.find(class_=re.compile('sc-product-row'))
        if not product_row:
            product_row = container
            
        desc = product_row.find(class_=re.compile(r'description-container'))
        if desc:
            el = self.find_element_flexible(desc, [
                {'type': 'class', 'value': 'label'},
                {'type': 'css', 'value': '.label'},
                {'type': 'custom', 'function': lambda p: self._get_longest_text(p)}
            ])
            if el:
                return el.get_text(strip=True)
            texts = [t.strip() for t in desc.stripped_strings if t.strip()]
            return max(texts, key=len, default='')
        return ""

    def extract_product_link(self, container):
        product_row = container.find(class_=re.compile('sc-product-row'))
        if not product_row:
            product_row = container
            
        el = self.find_element_flexible(product_row, [
            {'type': 'css', 'value': '.description-container a.redirect-row[href]'},
            {'type': 'custom', 'function': lambda p: p.find('a', href=re.compile(r'articulo\.mercadolibre'))}
        ])
        if el and el.get('href'):
            href = el['href']
            if href.startswith('/'):
                href = 'https://articulo.mercadolibre.com.uy' + href
            return href
        return ""

    def _from_srcset(self, srcset):
        try:
            parts = [p.strip() for p in srcset.split(',') if p.strip()]
            if parts:
                return parts[-1].split()[0]
        except Exception:
            pass
        return ''

    def _resolve_url(self, src):
        if not src:
            return ''
        src = src.strip().strip('"\'')
        if src.startswith('//'):
            return 'https:' + src
        if re.match(r'^https?://', src, re.I):
            return src
        if self.base_dir:
            full = (self.base_dir / src).resolve()
            if full.exists():
                return str(full)
        return src

    def extract_product_image(self, container):
        """Versión mejorada que busca imágenes de mejor calidad"""
        product_row = container.find(class_=re.compile('sc-product-row'))
        if not product_row:
            product_row = container
            
        img_strategies = [
            {'type': 'css', 'value': '.sc-product-picture__single-item img[src*="-I.jpg"]'},
            {'type': 'css', 'value': '.sc-product-picture__single-item img[src*="-O.jpg"]'},
            {'type': 'css', 'value': '.sc-product-picture__single-item img'},
            {'type': 'css', 'value': '.sc-product-picture img'},
            {'type': 'css', 'value': 'a[data-testid="redirect-img"] img'},
            {'type': 'custom', 'function': lambda p: p.find('img', src=re.compile(r'mlstatic.*\.(jpg|jpeg|png|webp)', re.I))},
            {'type': 'custom', 'function': lambda p: p.find('img')}
        ]
        
        img = self.find_element_flexible(product_row, img_strategies)
        if img:
            fallback_url = None
            for attr in ['src', 'data-src', 'data-lazy', 'data-original']:
                if img.get(attr):
                    url = self._resolve_url(img.get(attr))
                    if '-I.jpg' in url or '-O.jpg' in url:
                        return url
                    elif url:
                        fallback_url = url
            
            if img.get('srcset'):
                srcset_url = self._resolve_url(self._from_srcset(img['srcset']))
                if srcset_url:
                    return srcset_url
                    
            if fallback_url:
                return fallback_url
        
        div_bg = product_row.find(style=re.compile('background-image', re.I))
        if div_bg and div_bg.get('style'):
            m = re.search(r'url\(([^)]+)\)', div_bg['style'])
            if m:
                return self._resolve_url(m.group(1))
        
        img2 = product_row.find('img', src=re.compile('mlstatic'))
        if img2 and img2.get('src'):
            return self._resolve_url(img2['src'])
        
        return ""

    def extract_price(self, container):
        product_row = container.find(class_=re.compile('sc-product-row'))
        if not product_row:
            product_row = container
            
        el = self.find_element_flexible(product_row, [
            {'type': 'css', 'value': '.price-container .price'},
            {'type': 'class', 'value': 'price'},
            {'type': 'tag_text', 'tag': 'span', 'text_pattern': '$'}
        ])
        if el:
            txt = el.get_text(strip=True)
            if '$' in txt:
                return txt
        for txt in product_row.stripped_strings:
            if '$' in txt and any(c.isdigit() for c in txt):
                return txt
        return ""

    def extract_quantity(self, container):
        product_row = container.find(class_=re.compile('sc-product-row'))
        if not product_row:
            product_row = container
            
        el = self.find_element_flexible(product_row, [
            {'type': 'class', 'value': 'unit'},
            {'type': 'css', 'value': 'span.unit'},
            {'type': 'tag_text', 'tag': 'span', 'text_pattern': 'unidad'}
        ])
        if el:
            m = re.search(r'(\d+)', el.get_text(strip=True))
            if m:
                return m.group(1)
        for txt in product_row.stripped_strings:
            if 'unidad' in txt.lower():
                m = re.search(r'(\d+)', txt)
                if m:
                    return m.group(1)
        return ""

    def extract_sku(self, container):
        product_row = container.find(class_=re.compile('sc-product-row'))
        if not product_row:
            product_row = container
            
        el = self.find_element_flexible(product_row, [
            {'type': 'class', 'value': 'sku'},
            {'type': 'css', 'value': 'span.sku'},
            {'type': 'tag_text', 'tag': 'span', 'text_pattern': 'SKU:'}
        ])
        if el:
            txt = el.get_text(strip=True)
            if 'SKU:' in txt:
                return txt.split('SKU:')[-1].strip()
            return txt
        for txt in product_row.stripped_strings:
            if 'SKU:' in txt:
                return txt.split('SKU:')[-1].strip()
        return ""

    def extract_product(self, container):
        container_id = len(self.debug_report) + 1
        report = {
            'container_id': container_id,
            'html_classes': list(container.get('class', [])),
            'extraction_details': {},
            'status_detection': {},
            'temporal_analysis': {},  # NUEVO
            'final_result': {}
        }
        
        # Extraer datos del producto
        product = {}
        
        nombre = self.extract_product_name(container)
        product['nombre'] = nombre
        report['extraction_details']['nombre'] = {
            'value': nombre,
            'method_used': self._get_extraction_method_name(container, 'nombre'),
            'source_element': self._find_source_element_info(container, 'nombre')
        }
        
        link = self.extract_product_link(container)
        product['link'] = link
        report['extraction_details']['link'] = {
            'value': link,
            'method_used': self._get_extraction_method_name(container, 'link'),
            'source_element': self._find_source_element_info(container, 'link')
        }
        
        imagen = self.extract_product_image(container)
        product['imagen'] = imagen
        report['extraction_details']['imagen'] = {
            'value': imagen[:100] + '...' if len(str(imagen)) > 100 else imagen,
            'method_used': self._get_extraction_method_name(container, 'imagen'),
            'source_element': self._find_source_element_info(container, 'imagen')
        }
        
        precio = self.extract_price(container)
        product['precio'] = precio
        report['extraction_details']['precio'] = {
            'value': precio,
            'method_used': self._get_extraction_method_name(container, 'precio'),
            'source_element': self._find_source_element_info(container, 'precio')
        }
        
        cantidad = self.extract_quantity(container)
        product['cantidad'] = cantidad
        report['extraction_details']['cantidad'] = {
            'value': cantidad,
            'method_used': self._get_extraction_method_name(container, 'cantidad'),
            'source_element': self._find_source_element_info(container, 'cantidad')
        }
        
        sku = self.extract_sku(container)
        product['sku'] = sku
        report['extraction_details']['sku'] = {
            'value': sku,
            'method_used': self._get_extraction_method_name(container, 'sku'),
            'source_element': self._find_source_element_info(container, 'sku')
        }
        
        # ANÁLISIS DE ESTADO Y TEMPORAL
        status_raw = self.extract_order_status(container)
        order_id = self._extract_order_id(container)
        order_date = self._extract_order_date(container)
        should_filter, filter_reason = self.should_filter_product(container, product)
        
        # NUEVO: Análisis temporal para productos "a acordar"
        temporal_classification = self._classify_temporal_product(status_raw, order_date)
        
        report['status_detection'] = {
            'raw_status_found': status_raw,
            'order_id_found': order_id,
            'order_date_found': order_date.strftime('%d/%m/%Y %H:%M') if order_date else None,
            'status_elements_found': self._find_all_status_elements(container),
            'filter_states_checked': self.filter_states.copy(),
            'temporal_states_checked': self.temporal_states.copy(),
            'known_reprogrammed_products': self.known_reprogrammed_products.copy(),
            'known_delayed_products': self.known_delayed_products.copy(),
            'should_filter': should_filter,
            'filter_reason': filter_reason,
            'text_searched_in': container.get_text(separator=' ', strip=True)[:200] + '...'
        }
        
        # NUEVO: Reporte de análisis temporal
        report['temporal_analysis'] = {
            'temporal_threshold': self.temporal_threshold.strftime('%d/%m/%Y %H:%M'),
            'order_date': order_date.strftime('%d/%m/%Y %H:%M') if order_date else None,
            'is_temporal_state': temporal_classification is not None,
            'temporal_classification': temporal_classification,
            'comparison_result': None
        }
        
        if temporal_classification and order_date:
            report['temporal_analysis']['comparison_result'] = f"Pedido del {order_date.strftime('%d/%m/%Y %H:%M')} vs umbral {self.temporal_threshold.strftime('%d/%m/%Y %H:%M')}"
        
        # Determinar el tipo de producto y sus características
        is_urgent = temporal_classification == 'urgent'
        is_to_review = temporal_classification == 'to_review'
        
        product['is_urgent'] = is_urgent
        product['is_to_review'] = is_to_review
        product['temporal_classification'] = temporal_classification
        product['order_date'] = order_date.isoformat() if order_date else None
        
        # RESULTADO FINAL
        if should_filter:
            self.stats['filtered_out'] += 1
            if filter_reason in self.stats['filter_reasons']:
                self.stats['filter_reasons'][filter_reason] += 1
            else:
                self.stats['filter_reasons'][filter_reason] = 1
            
            filtered_product = product.copy()
            filtered_product['filter_reason'] = filter_reason
            self.filtered_products.append(filtered_product)
            
            report['final_result'] = {
                'action': 'FILTERED',
                'reason': filter_reason,
                'included_in_output': False,
                'temporal_classification': temporal_classification
            }
            
            logger.info(f"🚫 Producto filtrado por '{filter_reason}': {product['nombre'][:50]}...")
            self.debug_report.append(report)
            return None
        
        # Contar campos completos solo para productos no filtrados
        for k, v in product.items():
            if k in self.stats['fields_completeness'] and v:
                self.stats['fields_completeness'][k] += 1
                
        if product['nombre'] and product['precio']:
            self.stats['successfully_extracted'] += 1
            
            # NUEVO: Clasificar y contar por tipo
            if is_urgent:
                self.stats['urgent_count'] += 1
                self.urgent_products.append(product)
                report['final_result'] = {
                    'action': 'ACCEPTED_URGENT',
                    'reason': 'Has name and price, temporal state after threshold',
                    'included_in_output': True,
                    'section': 'URGENTES',
                    'temporal_classification': temporal_classification
                }
                logger.info(f"🔴 Producto URGENTE: {product['nombre'][:50]}...")
            elif is_to_review:
                self.stats['to_review_count'] += 1
                self.to_review_products.append(product)
                report['final_result'] = {
                    'action': 'ACCEPTED_TO_REVIEW',
                    'reason': 'Has name and price, temporal state before threshold',
                    'included_in_output': True,
                    'section': 'RETIROS A REVISAR',
                    'temporal_classification': temporal_classification
                }
                logger.info(f"🟡 Producto A REVISAR: {product['nombre'][:50]}...")
            else:
                self.stats['normal_count'] += 1
                self.products.append(product)
                report['final_result'] = {
                    'action': 'ACCEPTED_NORMAL',
                    'reason': 'Has name and price, normal state',
                    'included_in_output': True,
                    'section': 'NORMALES',
                    'temporal_classification': temporal_classification
                }
                logger.info(f"✅ Producto normal: {product['nombre'][:50]}...")
            
            self.debug_report.append(report)
            return product
        
        report['final_result'] = {
            'action': 'REJECTED',
            'reason': f"Missing name or price - Name: '{product.get('nombre', 'None')}', Price: '{product.get('precio', 'None')}'",
            'included_in_output': False,
            'temporal_classification': temporal_classification
        }
        logger.warning(f"Producto incompleto descartado: Nombre='{product.get('nombre', 'None')}', Precio='{product.get('precio', 'None')}'")
        self.debug_report.append(report)
        return None

    def _get_extraction_method_name(self, container, field_type):
        """Determina qué método se usó para extraer cada campo"""
        if field_type == 'nombre':
            return "extract_product_name -> label class or longest text"
        elif field_type == 'link':
            return "extract_product_link -> redirect-row href or articulo.mercadolibre"
        elif field_type == 'imagen':
            return "extract_product_image -> sc-product-picture img src"
        elif field_type == 'precio':
            return "extract_price -> price-container or $ pattern"
        elif field_type == 'cantidad':
            return "extract_quantity -> unit class or 'unidad' text"
        elif field_type == 'sku':
            return "extract_sku -> sku class or 'SKU:' text"
        return "unknown"

    def _find_source_element_info(self, container, field_type):
        """Encuentra información sobre el elemento fuente"""
        if field_type == 'nombre':
            product_row = container.find(class_=re.compile('sc-product-row')) or container
            desc = product_row.find(class_=re.compile(r'description-container'))
            if desc:
                label = desc.find(class_=re.compile('label'))
                if label:
                    return f"Found in: {label.name} with class='{' '.join(label.get('class', []))}'"
            return "Not found or fallback to longest text"
        elif field_type == 'precio':
            product_row = container.find(class_=re.compile('sc-product-row')) or container
            price_el = product_row.find(class_=re.compile('price'))
            if price_el:
                return f"Found in: {price_el.name} with class='{' '.join(price_el.get('class', []))}'"
            return "Not found in price elements, searched in text"
        return "Standard extraction method"

    def _find_all_status_elements(self, container):
        """Encuentra todos los elementos de estado en el contenedor"""
        status_elements = []
        
        status_classes = container.find_all(class_=re.compile('status'))
        for el in status_classes:
            status_elements.append({
                'element': el.name,
                'classes': list(el.get('class', [])),
                'text': el.get_text(strip=True)
            })
        
        specific_status = container.find_all(class_='sc-status-action-row__status')
        for el in specific_status:
            status_elements.append({
                'element': el.name,
                'classes': list(el.get('class', [])),
                'text': el.get_text(strip=True),
                'specific': 'sc-status-action-row__status'
            })
        
        return status_elements

    def get_all_products_organized(self):
        """
        NUEVO: Retorna todos los productos organizados en las tres categorías
        """
        return {
            'urgent': self.urgent_products,
            'normal': self.products,
            'to_review': self.to_review_products
        }

    def process_html(self, file_path):
        logger.info(f"Procesando: {file_path}")
        logger.info(f"🕐 Umbral temporal: {self.temporal_threshold.strftime('%d/%m/%Y %H:%M')}")
        
        if 'view-source' in file_path.name:
            raise ValueError("Archivo 'view-source' detectado. Guardá con Ctrl+S la página completa.")
        soup = self.load_html(file_path)

        # Buscar contenedores row-card-container
        order_containers = soup.find_all(class_='row-card-container')
        if not order_containers:
            order_containers = soup.find_all(class_=re.compile('row-card-container'))
        
        logger.info(f"📦 Contenedores row-card-container encontrados: {len(order_containers)}")
        
        if not order_containers:
            logger.info("No se encontraron row-card-container, buscando contenedores sc-row...")
            # NUEVO: Buscar directamente contenedores sc-row que son los principales
            sc_rows = soup.find_all(class_='sc-row')
            if not sc_rows:
                sc_rows = soup.find_all(class_=re.compile('sc-row'))
            
            if sc_rows:
                logger.info(f"📦 Contenedores sc-row encontrados: {len(sc_rows)}")
                order_containers = sc_rows
            else:
                logger.info("No se encontraron sc-row, buscando contenedores alternativos...")
                potential_containers = soup.find_all('div')
                
                for container in potential_containers:
                    has_status = container.find(class_=re.compile('sc-status-action-row__status'))
                    has_product = container.find(class_=re.compile('sc-product-row'))
                    
                    if has_status and has_product:
                        order_containers.append(container)
                        status_text = has_status.get_text(strip=True)
                        logger.debug(f"Contenedor alternativo encontrado - Estado: '{status_text}'")
        
        if not order_containers:
            logger.info("No se encontraron contenedores completos, usando estrategia de productos individuales")
            rows = soup.find_all(class_='sc-product-row')
            if not rows:
                rows = soup.find_all(class_=re.compile('sc-product-row'))
            if not rows:
                rows = soup.find_all(attrs={'data-testid': re.compile('product-row')})
            
            if not rows:
                cand = []
                for div in soup.find_all('div'):
                    if div.find('img') and div.find(text=re.compile(r'\$\s*\d+')):
                        cand.append(div)
                rows = cand
            order_containers = rows

        self.stats['total_found'] = len(order_containers)
        logger.info(f"📦 Total contenedores de pedido encontrados: {len(order_containers)}")
        
        if not order_containers:
            self._diagnose_structure(soup)

        for i, container in enumerate(order_containers, 1):
            try:
                status_element = container.find(class_=re.compile('sc-status-action-row__status'))
                product_element = container.find(class_=re.compile('sc-product-row'))
                
                status_text = status_element.get_text(strip=True) if status_element else "Sin estado"
                product_name = ""
                if product_element:
                    label_el = product_element.find(class_=re.compile('label'))
                    if label_el:
                        product_name = label_el.get_text(strip=True)[:30]
                
                logger.debug(f"Contenedor {i}: Estado='{status_text}' | Producto='{product_name}...'")
                
                p = self.extract_product(container)
                # El producto ya se agregó a la lista correspondiente en extract_product
            except Exception as e:
                logger.error(f"Error en contenedor {i}: {e}")

        # NUEVO: Calcular estadísticas finales
        total_valid_products = len(self.urgent_products) + len(self.products) + len(self.to_review_products)
        self.stats['final_count'] = total_valid_products
        
        self._log_summary()
        return self.get_all_products_organized()

    def _log_summary(self):
        logger.info("=== 📊 RESUMEN CON CLASIFICACIÓN TEMPORAL (FINES DE SEMANA) ===")
        current_day = datetime.now().strftime('%A')
        logger.info(f"🗓️  Día actual: {current_day}")
        logger.info(f"🕐 Umbral temporal usado: {self.temporal_threshold.strftime('%A %d/%m/%Y %H:%M')}")
        
        if datetime.now().weekday() == 0:  # Lunes
            logger.info("📅 MODO LUNES: Urgentes = pedidos 'a acordar' desde el viernes 16:00")
        else:
            logger.info("📅 MODO NORMAL: Urgentes = pedidos 'a acordar' desde ayer 16:00")
        logger.info(f"Total encontrados: {self.stats['total_found']}")
        logger.info(f"🚫 Filtrados por estado: {self.stats['filtered_out']}")
        if self.stats['filter_reasons']:
            for reason, count in self.stats['filter_reasons'].items():
                logger.info(f"  - {reason}: {count}")
        logger.info(f"🔴 URGENTES (a acordar después de umbral): {self.stats['urgent_count']}")
        logger.info(f"✅ NORMALES: {self.stats['normal_count']}")
        logger.info(f"🟡 A REVISAR (a acordar antes de umbral): {self.stats['to_review_count']}")
        logger.info(f"📋 Total válidos: {self.stats['final_count']}")
        
        total_valid = self.stats['urgent_count'] + self.stats['normal_count'] + self.stats['to_review_count']
        if total_valid > 0:
            for f, c in self.stats['fields_completeness'].items():
                logger.info(f"  {f}: {c}/{total_valid} ({c/total_valid*100:.1f}%)")

    def _diagnose_structure(self, soup):
        logger.info("=== 🔍 DIAGNÓSTICO HTML ===")
        
        row_card_containers = soup.find_all(class_='row-card-container')
        logger.info(f"row-card-container encontrados: {len(row_card_containers)}")
        
        prod_elems = soup.find_all(class_=re.compile('product', re.I))
        logger.info(f"Elems con 'product' en clase: {len(prod_elems)}")
        prices = soup.find_all(text=re.compile(r'\$\s*\d+'))
        logger.info(f"Textos con $: {len(prices)}")
        imgs = soup.find_all('img', src=re.compile('mlstatic'))
        logger.info(f"Imágenes mlstatic: {len(imgs)}")
        links = soup.find_all('a', href=re.compile('articulo.mercadolibre'))
        logger.info(f"Links artículo: {len(links)}")
        status_elems = soup.find_all(class_=re.compile('status'))
        logger.info(f"Elementos con 'status': {len(status_elems)}")
        
        ml_status = soup.find_all(class_='sc-status-action-row__status')
        logger.info(f"Estados ML HTML encontrados: {len(ml_status)}")
        if ml_status:
            for i, status in enumerate(ml_status[:5]):
                logger.info(f"  Estado HTML {i+1}: '{status.get_text(strip=True)}'")
        
        ml_products = soup.find_all(class_='sc-product-row')
        logger.info(f"Productos ML encontrados: {len(ml_products)}")
        
        containers_with_both = 0
        all_divs = soup.find_all('div')
        for div in all_divs:
            has_status = div.find(class_='sc-status-action-row__status')
            has_product = div.find(class_='sc-product-row')
            if has_status and has_product:
                containers_with_both += 1
                if containers_with_both <= 3:
                    status_text = has_status.get_text(strip=True)
                    logger.info(f"  Contenedor completo {containers_with_both}: Estado='{status_text}'")
        
        logger.info(f"Contenedores con estado Y producto: {containers_with_both}")
        
        # NUEVO: Diagnóstico de fechas
        date_patterns = [
            r'\d{1,2}\s+(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)',
            r'\d{1,2}\s+(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)\s+\d{4}',
            r'\d{1,2}\s+(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)\s+\d{1,2}:\d{2}'
        ]
        
        html_content = str(soup)
        date_matches = 0
        for pattern in date_patterns:
            matches = re.findall(pattern, html_content, re.I)
            date_matches += len(matches)
            if matches:
                logger.info(f"Fechas encontradas con patrón '{pattern}': {len(matches)}")
                for i, match in enumerate(matches[:3]):
                    logger.info(f"  Fecha {i+1}: {match}")
        
        logger.info(f"Total patrones de fecha encontrados: {date_matches}")
        
        scripts = soup.find_all('script')
        json_status_count = 0
        logger.info(f"Scripts encontrados: {len(scripts)}")
        
        for script in scripts:
            if script.string:
                status_matches = re.findall(r'"status"\s*:\s*"([^"]*)"', script.string, re.I)
                for match in status_matches:
                    # MEJORADO: Buscar más patrones de filtrado
                    if any(filter_word in match.lower() for filter_word in ['reprogramado', 'cancelad', 'devuelt', 'reembolsad', 'acordar', 'acuerda']):
                        json_status_count += 1
                        logger.info(f"  Estado JSON {json_status_count}: {match}")
                        if json_status_count >= 3:
                            break
            if json_status_count >= 3:
                break
        
        logger.info(f"Estados relevantes en JSON encontrados: {json_status_count}")

    def save_debug_report(self, dest):
        """Genera un reporte detallado en TXT de todo el proceso de extracción"""
        report_lines = [
            "=" * 80,
            "REPORTE DETALLADO DE EXTRACCIÓN - MERCADO LIBRE CON LÓGICA TEMPORAL",
            "=" * 80,
            f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}",
            f"Umbral temporal usado: {self.temporal_threshold.strftime('%d/%m/%Y %H:%M')}",
            f"Total contenedores procesados: {len(self.debug_report)}",
            f"Productos urgentes: {self.stats['urgent_count']}",
            f"Productos normales: {self.stats['normal_count']}",
            f"Productos a revisar: {self.stats['to_review_count']}",
            f"Productos filtrados: {self.stats['filtered_out']}",
            "",
            "LÓGICA TEMPORAL CON FINES DE SEMANA:",
            "-" * 40,
            f"- Día actual: {datetime.now().strftime('%A %d/%m/%Y')} (día {datetime.now().weekday() + 1} de la semana)",
            f"- Umbral calculado: {self.temporal_threshold.strftime('%A %d/%m/%Y %H:%M')}",
            "- REGLAS:",
            "  * LUNES: Urgentes = pedidos 'a acordar' después del VIERNES 16:00",
            "  * MARTES-VIERNES: Urgentes = pedidos 'a acordar' después de AYER 16:00", 
            "  * Productos 'a acordar' antes del umbral → A REVISAR",
            "",
            "FILTROS ACTIVOS:",
            "-" * 20
        ]
        
        for filter_state in self.filter_states:
            report_lines.append(f"- {filter_state}")
        
        report_lines.extend([
            "",
            "DETALLE POR CONTENEDOR:",
            "=" * 50
        ])
        
        for report in self.debug_report:
            report_lines.extend([
                "",
                f"CONTENEDOR #{report['container_id']}",
                "-" * 30,
                f"Clases del contenedor: {', '.join(report['html_classes']) if report['html_classes'] else 'Sin clases'}",
                "",
                "EXTRACCIÓN DE DATOS:",
                ""
            ])
            
            for field, details in report['extraction_details'].items():
                report_lines.extend([
                    f"  {field.upper()}:",
                    f"    Valor extraído: '{details['value']}'",
                    f"    Método usado: {details['method_used']}",
                    f"    Elemento fuente: {details['source_element']}",
                    ""
                ])
            
            status_info = report['status_detection']
            report_lines.extend([
                "DETECCIÓN DE ESTADO:",
                f"  Estado en bruto encontrado: '{status_info['raw_status_found']}'",
                f"  ID de pedido encontrado: '{status_info.get('order_id_found', 'No encontrado')}'",
                f"  Fecha de pedido encontrada: '{status_info.get('order_date_found', 'No encontrada')}'",
                f"  Elementos de estado encontrados: {len(status_info['status_elements_found'])}",
                ""
            ])
            
            if status_info['status_elements_found']:
                for i, elem in enumerate(status_info['status_elements_found'], 1):
                    report_lines.append(f"    Elemento {i}: <{elem['element']}> clases={elem['classes']} texto='{elem['text']}'")
                report_lines.append("")
            
            # NUEVO: Análisis temporal
            temporal_info = report['temporal_analysis']
            report_lines.extend([
                "ANÁLISIS TEMPORAL (CON FINES DE SEMANA):",
                f"  Día actual: {datetime.now().strftime('%A %d/%m/%Y')}",
                f"  Umbral temporal: {temporal_info['temporal_threshold']}",
                f"  Fecha del pedido: {temporal_info.get('order_date', 'No encontrada')}",
                f"  ¿Es estado temporal?: {temporal_info['is_temporal_state']}",
                f"  Clasificación temporal: {temporal_info.get('temporal_classification', 'No aplica')}",
                f"  Resultado comparación: {temporal_info.get('comparison_result', 'No aplica')}",
                ""
            ])
            
            report_lines.extend([
                f"  Filtros verificados: {', '.join(status_info['filter_states_checked'])}",
                f"  Estados temporales verificados: {', '.join(status_info.get('temporal_states_checked', []))}",
                f"  ¿Debe filtrarse?: {status_info['should_filter']}",
                f"  Razón del filtro: {status_info['filter_reason']}",
                "",
                f"  Texto completo del contenedor (primeros 200 chars):",
                f"    '{status_info['text_searched_in']}'",
                ""
            ])
            
            result = report['final_result']
            report_lines.extend([
                "RESULTADO FINAL:",
                f"  Acción: {result['action']}",
                f"  Razón: {result['reason']}",
                f"  Incluido en salida: {result['included_in_output']}",
                f"  Sección: {result.get('section', 'N/A')}",
                f"  Clasificación temporal: {result.get('temporal_classification', 'N/A')}",
                "",
                "=" * 50
            ])
        
        report_lines.extend([
            "",
            "RESUMEN ESTADÍSTICO:",
            "=" * 30,
            f"Total contenedores procesados: {len(self.debug_report)}",
            f"Productos urgentes: {len([r for r in self.debug_report if r['final_result']['action'] == 'ACCEPTED_URGENT'])}",
            f"Productos normales: {len([r for r in self.debug_report if r['final_result']['action'] == 'ACCEPTED_NORMAL'])}",
            f"Productos a revisar: {len([r for r in self.debug_report if r['final_result']['action'] == 'ACCEPTED_TO_REVIEW'])}",
            f"Productos filtrados: {len([r for r in self.debug_report if r['final_result']['action'] == 'FILTERED'])}",
            f"Productos rechazados (incompletos): {len([r for r in self.debug_report if r['final_result']['action'] == 'REJECTED'])}",
            "",
            "RAZONES DE FILTRADO:",
        ])
        
        for reason, count in self.stats['filter_reasons'].items():
            report_lines.append(f"  - {reason}: {count}")
        
        report_lines.extend([
            "",
            "COMPLETITUD DE CAMPOS:",
            ""
        ])
        
        total_products = self.stats['final_count']
        if total_products > 0:
            for field, count in self.stats['fields_completeness'].items():
                percentage = (count / total_products) * 100
                report_lines.append(f"  {field}: {count}/{total_products} ({percentage:.1f}%)")
        
        report_content = "\n".join(report_lines)
        dest.write_text(report_content, encoding='utf-8')
        logger.info(f"📋 Reporte detallado guardado en: {dest}")
        return dest

    def save_json(self, dest):
        """NUEVO: Guarda JSON organizado por secciones"""
        organized_products = self.get_all_products_organized()
        
        data = {
            'metadata': {
                'timestamp': datetime.now().isoformat(),
                'temporal_threshold': self.temporal_threshold.isoformat(),
                'total_productos_urgentes': len(organized_products['urgent']),
                'total_productos_normales': len(organized_products['normal']),
                'total_productos_a_revisar': len(organized_products['to_review']),
                'productos_filtrados': self.stats['filtered_out'],
                'filtros_aplicados': self.filter_states,
                'estados_temporales': self.temporal_states,
                'razon_filtrado': self.stats['filter_reasons'],
                'fuente': 'Mercado Libre Uruguay'
            },
            'productos_urgentes': organized_products['urgent'],
            'productos_normales': organized_products['normal'],
            'productos_a_revisar': organized_products['to_review'],
            'productos_filtrados': self.filtered_products
        }
        dest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        logger.info(f"JSON guardado en: {dest}")
        return dest


# ===================== PDF MEJORADO CON SECCIONES ===================== #
class PDFGenerator:
    def __init__(self, organized_products):
        """
        organized_products: dict con keys 'urgent', 'normal', 'to_review'
        """
        # ---- Imports internos para que la clase sea autosuficiente ----
        import logging, os, re, time, tempfile, requests
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER
        from reportlab.platypus import (
            SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
            PageBreak, Image as RLImage, KeepTogether
        )
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        # Referencias a módulos/objs usados en métodos
        self.os = os
        self.re = re
        self.time = time
        self.tempfile = tempfile
        self.requests = requests

        self.colors = colors
        self.mm = mm
        self.getSampleStyleSheet = getSampleStyleSheet
        self.ParagraphStyle = ParagraphStyle
        self.TA_CENTER = TA_CENTER

        self.SimpleDocTemplate = SimpleDocTemplate
        self.Table = Table
        self.TableStyle = TableStyle
        self.Paragraph = Paragraph
        self.Spacer = Spacer
        self.PageBreak = PageBreak
        self.RLImage = RLImage
        self.KeepTogether = KeepTogether

        self.pdfmetrics = pdfmetrics
        self.TTFont = TTFont

        # ---- Estado / configuración ----
        self.organized_products = organized_products
        self.image_downloader = self._init_image_downloader()
        self.logger = logging.getLogger(__name__)

        # Ajustes para impresión térmica B/N
        self.thermal_bw = True        # activar procesado para B/N
        self.thermal_dpi = 203        # 203 o 300
        self.thermal_algo = "floyd"   # "floyd" (recomendado) o "threshold"
        self.threshold_level = 180    # umbral si thermal_algo == "threshold"

        # ---- Ajustes tipográficos para los "datos" (Precio, Cantidad, SKU) ----
        self.data_font_size = 12      # antes 10 → +20 %
        self.data_leading   = 14      # interlineado acorde

        # Intentar un "peso medio" real si está disponible:
        self.data_font_name = "Helvetica"  # fallback
        # Fuente en negrita para "X Unidades"
        self.bold_font_name = "Helvetica-Bold"

        try:
            # Busca Roboto-Medium.ttf y Roboto-Bold.ttf junto al script o en ./fonts/
            script_dir = os.path.dirname(os.path.abspath(__file__))
            candidates_medium = [
                os.path.join(script_dir, "Roboto-Medium.ttf"),
                os.path.join(script_dir, "fonts", "Roboto-Medium.ttf"),
            ]
            for path in candidates_medium:
                if os.path.exists(path):
                    self.pdfmetrics.registerFont(self.TTFont("Roboto-Medium", path))
                    self.data_font_name = "Roboto-Medium"   # semi-bold real (sin llegar a Bold)
                    break
        except Exception:
            pass

        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            candidates_bold = [
                os.path.join(script_dir, "Roboto-Bold.ttf"),
                os.path.join(script_dir, "fonts", "Roboto-Bold.ttf"),
            ]
            for path in candidates_bold:
                if os.path.exists(path):
                    self.pdfmetrics.registerFont(self.TTFont("Roboto-Bold", path))
                    self.bold_font_name = "Roboto-Bold"
                    break
        except Exception:
            pass

        # Temporales a limpiar tras doc.build()
        self._temp_files = []

    # ---------------------------------------
    # Sesión HTTP para imágenes
    # ---------------------------------------
    def _init_image_downloader(self):
        """Inicializa el descargador de imágenes mejorado."""
        session = self.requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
            'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Referer': 'https://www.mercadolibre.com.uy/',
            'Sec-Fetch-Dest': 'image',
            'Sec-Fetch-Mode': 'no-cors',
            'Sec-Fetch-Site': 'cross-site',
        })
        return session

    def _register_temp(self, path):
        """Registra un archivo temporal para borrarlo al final."""
        if path and self.os.path.exists(path):
            self._temp_files.append(path)

    def _cleanup_temps(self):
        """Borra todos los temporales registrados."""
        for p in self._temp_files:
            try:
                if self.os.path.exists(p):
                    self.os.unlink(p)
            except Exception:
                pass
        self._temp_files.clear()

    # ---------------------------------------
    # Utilidades para URLs de Mercado Libre
    # ---------------------------------------
    def _clean_ml_url(self, url):
        """Limpia variantes de ML para mejorar compatibilidad."""
        if not url:
            return url
        try:
            if '.webp' in url:
                url = url.replace('.webp', '.jpg')
            if '-O.jpg' in url:
                url = url.replace('-O.jpg', '-I.jpg')
            return url
        except Exception:
            return url

    def _try_image_variants(self, url):
        """Genera variantes de la URL a probar."""
        variants = [url]
        if 'mlstatic.com' in url:
            base_url = url
            variants.extend([
                base_url.replace('-O.jpg', '-I.jpg'),
                base_url.replace('-O.jpg', '-S.jpg'),
                base_url.replace('-O.webp', '-I.jpg'),
                base_url.replace('-O.webp', '-S.jpg'),
                base_url.replace('.webp', '.jpg'),
            ])
        # eliminar duplicados preservando orden
        return list(dict.fromkeys(variants))

    # ---------------------------------------
    # Procesado para térmica B/N (Pillow)
    # ---------------------------------------
    def _process_image_for_thermal(self, src_path, width_pt, height_pt):
        """
        Prepara la imagen para impresión térmica B/N:
        - escala de grises + autocontraste
        - subida de contraste + nitidez (unsharp)
        - realce de bordes (mezcla)
        - 1-bit con dithering (o umbral fijo)
        - redimensionado al ancho objetivo según DPI
        Devuelve la ruta de un PNG temporal listo para ReportLab.
        """
        try:
            from PIL import Image, ImageOps, ImageEnhance, ImageFilter
            dpi = getattr(self, 'thermal_dpi', 203)
            algo = getattr(self, 'thermal_algo', 'floyd')

            # width_pt está en puntos PDF; 72 pt = 1 inch
            target_w_px = max(140, int((width_pt / 72.0) * dpi))

            with Image.open(src_path) as im:
                im = im.convert('RGB')
                # Redimensionar manteniendo aspecto antes de binarizar
                im.thumbnail((target_w_px, 10_000), Image.LANCZOS)

                g = im.convert('L')
                g = ImageOps.autocontrast(g, cutoff=2)
                g = ImageEnhance.Contrast(g).enhance(1.6)
                g = g.filter(ImageFilter.UnsharpMask(radius=1.2, percent=160, threshold=4))

                # Realce de bordes y mezcla suave
                edges = g.filter(ImageFilter.FIND_EDGES)
                edges = ImageOps.autocontrast(edges, cutoff=2)
                g = Image.blend(g, edges, 0.35)

                # Binarización
                if algo == 'threshold':
                    thr = int(getattr(self, 'threshold_level', 180))
                    bw = g.point(lambda p: 255 if p > thr else 0, mode='1')
                else:  # floyd-steinberg
                    bw = g.convert('1', dither=Image.FLOYDSTEINBERG)

                # Borde negro fino para reforzar contornos generales
                bw = ImageOps.expand(bw, border=1, fill=0)

                out_path = self.tempfile.NamedTemporaryFile(delete=False, suffix='.png').name
                bw.save(out_path, 'PNG')
                self._register_temp(out_path)
                return out_path

        except Exception as e:
            self.logger.warning(f"Procesado B/N falló, uso original. Detalle: {e}")
            return src_path

    # ---------------------------------------
    # Descarga y preparación de imágenes
    # ---------------------------------------
    def _download_image_improved(self, url, width, height, max_retries=3):
        """Descarga imagen con múltiples estrategias y prepara para térmica B/N."""
        if not url:
            return self._placeholder(width, height)

        # Ruta local
        if self.os.path.exists(str(url)):
            try:
                img_path = str(url)
                if getattr(self, 'thermal_bw', False):
                    img_path = self._process_image_for_thermal(img_path, width, height)
                img = self.RLImage(img_path, width=width, height=height, kind='proportional')
                return img
            except Exception as e:
                self.logger.warning(f"Error imagen local {url}: {e}")
                return self._placeholder(width, height)

        # Cache global
        cache = globals().setdefault('_IMAGE_CACHE', {})
        cache_key = f"{url}_{width}_{height}"
        if cache_key in cache:
            return cache[cache_key]

        variants = self._try_image_variants(self._clean_ml_url(url))

        for variant_url in variants:
            self.logger.debug(f"Probando URL: {variant_url[:60]}...")
            for attempt in range(max_retries):
                try:
                    headers = self.image_downloader.headers.copy()
                    response = self.image_downloader.get(
                        variant_url,
                        headers=headers,
                        timeout=15,
                        stream=True
                    )
                    if response.status_code == 200:
                        content_type = response.headers.get('content-type', '').lower()
                        if 'image' in content_type:
                            image_data = response.content
                            if len(image_data) > 1000:
                                # Guardar temporal original
                                tmp_jpg = self.tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
                                tmp_jpg.write(image_data)
                                tmp_jpg.flush()
                                tmp_jpg.close()
                                self._register_temp(tmp_jpg.name)

                                # Procesar para térmica si corresponde
                                img_path = tmp_jpg.name
                                if getattr(self, 'thermal_bw', False):
                                    img_path = self._process_image_for_thermal(tmp_jpg.name, width, height)

                                try:
                                    img = self.RLImage(img_path, width=width, height=height, kind='proportional')
                                    cache[cache_key] = img
                                    self.logger.info(f"✅ Imagen preparada: {variant_url[:50]}...")
                                    return img
                                except Exception as e:
                                    self.logger.warning(f"Error creando RLImage: {e}")
                            else:
                                self.logger.warning(f"Imagen muy pequeña: {len(image_data)} bytes")
                        else:
                            self.logger.warning(f"No es imagen: {content_type}")
                    else:
                        self.logger.warning(f"HTTP {response.status_code}: {variant_url[:50]}...")
                except self.requests.exceptions.Timeout:
                    self.logger.warning(f"Timeout intento {attempt + 1}/{max_retries}")
                    if attempt < max_retries - 1:
                        self.time.sleep(1)
                except Exception as e:
                    self.logger.warning(f"Error descarga intento {attempt + 1}: {e}")
                    if attempt < max_retries - 1:
                        self.time.sleep(0.5)
                    else:
                        break
            self.logger.debug(f"Falló variante: {variant_url[:50]}...")

        self.logger.error(f"❌ No se pudo descargar imagen: {url[:50]}...")
        return self._placeholder(width, height)

    def _download_or_open_image(self, url, width, height):
        """Método principal para obtener imagen o placeholder."""
        return self._download_image_improved(url, width, height)

    # ---------------------------------------
    # Placeholders y banners B/N
    # ---------------------------------------
    def _placeholder(self, w, h):
        """Placeholder 100% blanco y negro (sin grises)."""
        ph_style = self.ParagraphStyle(
            'ph',
            fontName=self.data_font_name,      # usar mismo font
            fontSize=self.data_font_size,      # consistente con datos
            alignment=self.TA_CENTER,
            textColor=self.colors.black,
            leading=self.data_leading
        )
        tbl = self.Table(
            [[self.Paragraph('<b>[Imagen no disponible]</b><br/><font size="9">Verifique conexión</font>', ph_style)]],
            colWidths=[w], rowHeights=[h]
        )
        tbl.setStyle(self.TableStyle([
            ('BOX', (0, 0), (-1, -1), 1.2, self.colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BACKGROUND', (0, 0), (-1, -1), self.colors.white),
        ]))
        return tbl

    def _build_bw_banner(self, text, width, height_mm=9, font_size=11):
        """Crea una barra superior negra con texto blanco centrado (compacta)."""
        banner_style = self.ParagraphStyle(
            'banner',
            fontName='Helvetica-Bold',
            fontSize=font_size,
            alignment=self.TA_CENTER,
            textColor=self.colors.white,
            leading=font_size + 2
        )
        banner_tbl = self.Table(
            [[self.Paragraph(text.upper(), banner_style)]],
            colWidths=[width],
            rowHeights=[height_mm * self.mm]
        )
        banner_tbl.setStyle(self.TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), self.colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING',  (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING',   (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING',(0, 0), (-1, -1), 0),
        ]))
        return banner_tbl

    def _wrap_with_banner(self, inner_tbl, banner_text, total_width, content_pad=6):
        """
        Envuelve la tabla del producto con:
          - Fila superior: banner negro (sin padding para no desbordar)
          - Fila inferior: contenido del producto
          - Borde negro alrededor
        """
        outer = self.Table(
            [[self._build_bw_banner(banner_text, total_width)],
             [inner_tbl]],
            colWidths=[total_width]
        )
        outer.hAlign = 'LEFT'
        outer.setStyle(self.TableStyle([
            ('BOX', (0, 0), (-1, -1), 2, self.colors.black),

            # Sin padding en la fila del banner
            ('LEFTPADDING',  (0, 0), (-1, 0), 0),
            ('RIGHTPADDING', (0, 0), (-1, 0), 0),
            ('TOPPADDING',   (0, 0), (-1, 0), 0),
            ('BOTTOMPADDING',(0, 0), (-1, 0), 0),

            # Padding solo para el contenido
            ('LEFTPADDING',  (0, 1), (-1, 1), content_pad),
            ('RIGHTPADDING', (0, 1), (-1, 1), content_pad),
            ('TOPPADDING',   (0, 1), (-1, 1), content_pad),
            ('BOTTOMPADDING',(0, 1), (-1, 1), content_pad),

            ('BACKGROUND', (0, 1), (-1, 1), self.colors.white),
        ]))
        return outer

    # ---------------------------------------
    # Construcción de un bloque de producto
    # ---------------------------------------
    def _create_product_table(self, product, img_w, img_h, data_width, pad,
                              is_urgent=False, is_to_review=False):
        """
        Crea la tabla de un producto individual.
        Para URGENTE y A REVISAR se devuelve envuelta con un banner B/N.

        Ajuste solicitado:
        - Si cantidad > 1: mostrar "X Unidades" en NEGRITA.
        - Si cantidad >= 2: alinear a la izquierda como los otros valores.
        """
        img_el = self._download_or_open_image(product.get('imagen', ''), width=img_w, height=img_h)

        qty_raw = product.get('cantidad', '1')
        try:
            qty_int = int(self.re.findall(r'\d+', str(qty_raw))[0])
        except Exception:
            qty_int = 1

        # ---- Estilos de datos (20% más grandes, "peso medio" si hay Roboto-Medium) ----
        label_style = self.ParagraphStyle(
            'data_label',
            fontName=self.data_font_name,
            fontSize=self.data_font_size,
            leading=self.data_leading,
        )
        value_style = self.ParagraphStyle(
            'data_value',
            fontName=self.data_font_name,
            fontSize=self.data_font_size,
            leading=self.data_leading,
        )

        # Construcción del valor de Cantidad según la regla
        if qty_int == 1:
            qty_cell = self.Paragraph("1 unidad", value_style)
        else:
            # "X Unidades" en NEGRITA siempre que sea > 1
            qty_bold_style = self.ParagraphStyle(
                'qty_bold',
                fontName=self.bold_font_name,            # Roboto-Bold si disponible, si no Helvetica-Bold
                fontSize=int(round(14 * 1.2)),           # mantener énfasis de tamaño existente
                leading=int(round(16 * 1.2)),
                alignment=0  # Alineación izquierda para consistencia con otros valores
            )
            qty_cell = self.Paragraph(f"{qty_int} Unidades", qty_bold_style)

        datos = [
            [self.Paragraph('Precio:', label_style),
             self.Paragraph(str(product.get('precio', 'No disponible')), value_style)],
            [self.Paragraph('Cantidad:', label_style),
             qty_cell],
            [self.Paragraph('SKU:', label_style),
             self.Paragraph(str(product.get('sku', 'No disponible')), value_style)],
        ]

        data_tbl = self.Table(datos, colWidths=[75, data_width - 75])

        # Estilo base
        data_style_cmds = [
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]

        # Si cantidad >= 2: aplicar estilo especial para destacar
        if qty_int >= 2:
            data_style_cmds.extend([
                ('ALIGN', (1, 1), (1, 1), 'LEFT'),        # Solo el valor "X Unidades" alineado a la izquierda
                ('VALIGN', (0, 1), (1, 1), 'MIDDLE'),     # Centrar verticalmente
                ('TOPPADDING', (0, 1), (1, 1), 4),        # Más padding superior
                ('BOTTOMPADDING', (0, 1), (1, 1), 4),     # Más padding inferior
            ])

        data_tbl.setStyle(self.TableStyle(data_style_cmds))

        # Tabla principal (imagen + datos) sin padding (lo maneja el wrapper)
        main_tbl = self.Table([[img_el, data_tbl]], colWidths=[50 * self.mm, data_width])
        main_tbl.setStyle(self.TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (0, 0), (0, 0), 'CENTER'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))

        total_width = (50 * self.mm) + data_width

        if is_urgent:
            return self._wrap_with_banner(main_tbl, "URGENTE", total_width, content_pad=pad)
        elif is_to_review:
            return self._wrap_with_banner(main_tbl, "A REVISAR", total_width, content_pad=pad)
        else:
            # Normal: marco fino B/N
            main_tbl.setStyle(self.TableStyle([
                ('BOX', (0, 0), (-1, -1), 1, self.colors.black),
                ('BACKGROUND', (0, 0), (-1, -1), self.colors.white),
            ]))
            return main_tbl

    # ---------------------------------------
    # Generación del documento completo
    # ---------------------------------------
    def generate(self, dest):
        """Genera el PDF con secciones organizadas y limpia temporales al final."""
        from datetime import datetime

        doc = self.SimpleDocTemplate(
            str(dest),
            pagesize=(595.275590551, 841.88976378),  # A4
            rightMargin=19 * self.mm,
            leftMargin=19 * self.mm,
            topMargin=19 * self.mm,
            bottomMargin=19 * self.mm
        )

        styles = self.getSampleStyleSheet()

        title_style = self.ParagraphStyle(
            'title',
            parent=styles['Heading1'],
            fontSize=18,
            alignment=self.TA_CENTER,
            textColor=self.colors.HexColor('#333333'),
            spaceAfter=12
        )

        section_title_style = self.ParagraphStyle(
            'section_title',
            parent=styles['Heading1'],
            fontSize=16,
            alignment=self.TA_CENTER,
            textColor=self.colors.black,
            spaceAfter=10,
            spaceBefore=15
        )

        prod_title_style = self.ParagraphStyle(
            'ptitle',
            parent=styles['Heading2'],
            fontSize=14,
            textColor=self.colors.black,
            spaceAfter=6,
            keepWithNext=True
        )

        story = []

        # Header principal
        story.append(self.Paragraph("PRODUCTOS MERCADO LIBRE - ORGANIZADOS POR PRIORIDAD", title_style))

        total_products = (
            len(self.organized_products.get('urgent', [])) +
            len(self.organized_products.get('normal', [])) +
            len(self.organized_products.get('to_review', []))
        )
        info = f"Fecha de generación: {datetime.now().strftime('%d/%m/%Y %H:%M')}<br/>"
        info += f"Urgentes: {len(self.organized_products.get('urgent', []))} | "
        info += f"Normales: {len(self.organized_products.get('normal', []))} | "
        info += f"A revisar: {len(self.organized_products.get('to_review', []))}<br/>"
        info += f"Total de productos: {total_products}"

        story.append(self.Paragraph(info, styles['Normal']))
        story.append(self.Spacer(1, 8))
        story.append(self.Table([['']], colWidths=[doc.width],
                           style=self.TableStyle([('LINEBELOW', (0, 0), (-1, -1), 1, self.colors.black)])))
        story.append(self.Spacer(1, 12))

        # Configuración de imagen y layout
        IMG_BASE = 55 * self.mm
        SCALE = 0.49
        IMG_W = IMG_BASE * SCALE
        IMG_H = IMG_BASE * SCALE
        DATA_WIDTH = doc.width - (50 * self.mm)

        BLOCK_SPACER = 10
        PAD = 6

        try:
            # SECCIÓN 1: URGENTES
            if self.organized_products.get('urgent'):
                story.append(self.Paragraph("PRODUCTOS URGENTES", section_title_style))
                story.append(self.Paragraph("(A acordar después de ayer 16:00 - Requieren atención inmediata)", styles['Normal']))
                story.append(self.Spacer(1, 12))
                for i, product in enumerate(self.organized_products['urgent'], 1):
                    name = product.get('nombre') or f"Producto Urgente {i}"
                    block_tbl = self._create_product_table(product, IMG_W, IMG_H, DATA_WIDTH, PAD, is_urgent=True)
                    story.append(self.KeepTogether([
                        self.Paragraph(name, prod_title_style),
                        block_tbl,
                        self.Spacer(1, BLOCK_SPACER)
                    ]))
                story.append(self.PageBreak())

            # SECCIÓN 2: NORMALES
            if self.organized_products.get('normal'):
                story.append(self.Paragraph("PRODUCTOS NORMALES", section_title_style))
                story.append(self.Spacer(1, 12))
                for i, product in enumerate(self.organized_products['normal'], 1):
                    name = product.get('nombre') or f"Producto {i}"
                    block_tbl = self._create_product_table(product, IMG_W, IMG_H, DATA_WIDTH, PAD)
                    story.append(self.KeepTogether([
                        self.Paragraph(name, prod_title_style),
                        block_tbl,
                        self.Spacer(1, BLOCK_SPACER)
                    ]))
                if self.organized_products.get('to_review'):
                    story.append(self.PageBreak())

            # SECCIÓN 3: A REVISAR
            if self.organized_products.get('to_review'):
                story.append(self.Paragraph("RETIROS A REVISAR", section_title_style))
                story.append(self.Paragraph("(A acordar antes de ayer 16:00 - Revisar estado con compradores)", styles['Normal']))
                story.append(self.Spacer(1, 12))
                for i, product in enumerate(self.organized_products['to_review'], 1):
                    name = product.get('nombre') or f"Producto a Revisar {i}"
                    block_tbl = self._create_product_table(product, IMG_W, IMG_H, DATA_WIDTH, PAD, is_to_review=True)
                    story.append(self.KeepTogether([
                        self.Paragraph(name, prod_title_style),
                        block_tbl,
                        self.Spacer(1, BLOCK_SPACER)
                    ]))

            # Construir PDF
            doc.build(story)
            self.logger.info(f"PDF generado con secciones organizadas en: {dest}")
            return dest

        finally:
            # Limpieza de temporales (tras terminar de construir el PDF)
            self._cleanup_temps()

# ===================== DETECTOR DE TECLAS GH ===================== #
class MLKeyDetector:
    def __init__(self):
        self.is_listening = False
        self.callback = None
        self.g_pressed = False
        self.h_pressed = False
        
    def start_detection(self, callback):
        """Iniciar detección de teclas G+H"""
        self.callback = callback
        self.is_listening = True
        self.g_pressed = False
        self.h_pressed = False
        
        # Configurar listeners de teclas
        keyboard.on_press_key('g', self._on_g_press)
        keyboard.on_press_key('h', self._on_h_press)
        keyboard.on_release_key('g', self._on_g_release)
        keyboard.on_release_key('h', self._on_h_release)
        
        logging.info("🎯 Detector de teclas G+H iniciado - Presiona G y H juntas para activar")
        
    def _on_g_press(self, event):
        """Cuando se presiona G"""
        if self.is_listening:
            self.g_pressed = True
            logging.info("🔤 Tecla G presionada")
            self._check_combination()
    
    def _on_h_press(self, event):
        """Cuando se presiona H"""
        if self.is_listening:
            self.h_pressed = True
            logging.info("🔤 Tecla H presionada")
            self._check_combination()
    
    def _on_g_release(self, event):
        """Cuando se suelta G"""
        self.g_pressed = False
    
    def _on_h_release(self, event):
        """Cuando se suelta H"""
        self.h_pressed = False
    
    def _check_combination(self):
        """Verificar si ambas teclas están presionadas"""
        if self.g_pressed and self.h_pressed:
            logging.info("✅ Combinación G+H detectada - Activando descarga automática")
            self.stop_detection()
            if self.callback:
                self.callback()
    
    def stop_detection(self):
        """Detener detección"""
        self.is_listening = False
        # Remover listeners
        keyboard.unhook_all()

class MLAutoDownloader:
    def __init__(self):
        # Crear directorio temporal para cache
        self.cache_dir = Path.home() / ".ml_cache"
        self.cache_dir.mkdir(exist_ok=True)
        self.last_downloaded_file = None  # NUEVO: Guardar referencia al último archivo descargado
        
    def download_current_page(self):
        """Descargar la página actual automáticamente a cache"""
        try:
            # Limpiar cache anterior
            self._clean_cache()
            
            # Generar nombre de archivo con timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"ML_Ventas_{timestamp}.html"
            cache_file = self.cache_dir / filename
            
            logging.info(f"🔄 Iniciando descarga automática...")
            logging.info(f"📁 Archivo destino: {cache_file}")
            
            # Método 1: Intentar con Ctrl+S (más confiable)
            pyautogui.hotkey('ctrl', 's')
            time.sleep(3)  # Más tiempo para que aparezca el diálogo
            
            # Escribir solo el nombre del archivo (sin ruta completa)
            pyautogui.write(filename)
            time.sleep(2)  # Más tiempo para escribir
            pyautogui.press('enter')
            time.sleep(8)  # Mucho más tiempo para que se complete la descarga
            
            # Verificar si se guardó (con múltiples intentos)
            for attempt in range(5):  # Intentar 5 veces
                if cache_file.exists():
                    logging.info(f"💾 Página guardada exitosamente: {filename}")
                    self.last_downloaded_file = cache_file
                    return cache_file
                logging.info(f"🔄 Esperando descarga... intento {attempt + 1}/5")
                time.sleep(2)  # Esperar 2 segundos más entre intentos
            
            # Método 2: Si no funcionó, buscar archivos HTML recientes en el escritorio
            desktop = Path.home() / "Desktop"
            html_files = list(desktop.glob("*.html"))
            
            if html_files:
                # Ordenar por fecha de modificación (más reciente primero)
                html_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
                latest_file = html_files[0]
                
                # Verificar que el archivo sea reciente (últimos 60 segundos)
                file_time = datetime.fromtimestamp(latest_file.stat().st_mtime)
                if (datetime.now() - file_time).seconds < 60:
                    logging.info(f"💾 Archivo reciente encontrado en escritorio: {latest_file.name}")
                    # Mover al cache
                    new_path = cache_file
                    latest_file.rename(new_path)
                    self.last_downloaded_file = new_path
                    return new_path
                else:
                    logging.warning(f"⚠️ Archivo en escritorio es muy viejo: {latest_file.name} ({(datetime.now() - file_time).seconds}s)")
            
            # Método 3: Buscar en el directorio de descargas
            downloads = Path.home() / "Downloads"
            if downloads.exists():
                download_html_files = list(downloads.glob("*.html"))
                if download_html_files:
                    download_html_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
                    latest_download = download_html_files[0]
                    
                    file_time = datetime.fromtimestamp(latest_download.stat().st_mtime)
                    if (datetime.now() - file_time).seconds < 60:
                        logging.info(f"💾 Archivo reciente encontrado en descargas: {latest_download.name}")
                        new_path = cache_file
                        latest_download.rename(new_path)
                        self.last_downloaded_file = new_path
                        return new_path
            
            logging.error("❌ No se pudo descargar el archivo automáticamente")
            logging.info("💡 SOLUCIÓN MANUAL:")
            logging.info("   1. Presiona Ctrl+S en tu navegador")
            logging.info("   2. Guarda el archivo como 'ML_Ventas_[fecha].html' en el escritorio")
            logging.info("   3. Usa el botón 'Seleccionar archivo' en la interfaz")
            return None
            
        except Exception as e:
            logging.error(f"Error descargando página: {e}")
            return None
    
    def _clean_cache(self):
        """Limpiar archivos anteriores del cache"""
        try:
            for file in self.cache_dir.glob("*.html"):
                file.unlink()
            logging.info("🧹 Cache limpiado")
        except Exception as e:
            logging.error(f"Error limpiando cache: {e}")
    
    def cleanup_after_processing(self):
        """Limpiar cache después del procesamiento"""
        try:
            # NUEVO: Borrar el archivo específico que se descargó
            if self.last_downloaded_file and self.last_downloaded_file.exists():
                self.last_downloaded_file.unlink()
                logging.info(f"🗑️ Archivo descargado eliminado: {self.last_downloaded_file.name}")
                self.last_downloaded_file = None
            
            # Limpiar cualquier otro archivo en cache
            self._clean_cache()
            logging.info("🧹 Cache limpiado después del procesamiento")
        except Exception as e:
            logging.error(f"Error limpiando cache: {e}")

# ===================== GUI ACTUALIZADA ===================== #
class MLExtractorGUI:
    def __init__(self, args):
        self.args = args
        self.root = tk.Tk()
        self.root.title("Extractor ML Uruguay - CON FILTROS Y LÓGICA TEMPORAL")
        self.root.geometry("700x720")
        self.root.resizable(False, False)

        self.file_path = tk.StringVar()
        self.status_text = tk.StringVar(value="Esperando archivo...")
        self.progress_var = tk.DoubleVar()
        
        # Variables para filtros
        self.filter_reprogramados = tk.BooleanVar(value=True)
        self.filter_cancelados = tk.BooleanVar(value=True)
        self.filter_devueltos = tk.BooleanVar(value=True)
        self.filter_demorados = tk.BooleanVar(value=True)

        self.selected_file = None

        self._build_ui()

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=20)
        main.grid(row=0, column=0, sticky='nsew')

        ttk.Label(main, text="Extractor ML - FILTROS + LÓGICA TEMPORAL",
                  font=('Helvetica', 18, 'bold')).grid(row=0, column=0, columnspan=2, pady=(0, 15))

        # Explicación de la lógica temporal
        temporal_info = (
            "🕐 LÓGICA TEMPORAL CON FINES DE SEMANA:\n"
            "• LUNES: Productos 'a acordar' después del VIERNES 16:00 → URGENTES\n"
            "• MAR-VIE: Productos 'a acordar' después de AYER 16:00 → URGENTES\n"
            "• Productos 'a acordar' anteriores al umbral → RETIROS A REVISAR\n"
            "• Resto de productos → Sección normal"
        )
        temporal_label = ttk.Label(main, text=temporal_info, justify=tk.LEFT,
                                   font=('Helvetica', 9), foreground='blue')
        temporal_label.grid(row=1, column=0, columnspan=2, pady=(0, 15), sticky='w')

        instr = (
            "OPCIÓN 1 - DESCARGAR AUTOMÁTICAMENTE:\n"
            "1. El detector G+H está ACTIVO automáticamente\n"
            "2. Ve a tu navegador con ML abierto\n"
            "3. Navega a tus ventas/pedidos\n"
            "4. Presiona G y H JUNTAS en la página\n"
            "5. Se descargará automáticamente\n"
            "6. Se procesará automáticamente\n"
            "7. El programa se cerrará al terminar\n\n"
            "OPCIÓN 2 - MANUAL:\n"
            "1. Ir al panel de vendedor en Mercado Libre\n"
            "2. Sección Ventas/Pedidos\n"
            "3. Guardar la página completa como HTML (Ctrl+S)\n"
            "4. Seleccionar filtros deseados\n"
            "5. Seleccionar el archivo aquí"
        )
        ttk.Label(main, text=instr, justify=tk.LEFT,
                  font=('Helvetica', 10)).grid(row=2, column=0, columnspan=2, pady=(0, 15), sticky='w')

        # Frame de filtros
        filter_frame = ttk.LabelFrame(main, text="Filtros (Estados a Excluir)", padding=10)
        filter_frame.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(0, 15))
        
        ttk.Checkbutton(filter_frame, text="Reprogramados por el comprador", 
                       variable=self.filter_reprogramados).grid(row=0, column=0, sticky='w', pady=2)
        ttk.Checkbutton(filter_frame, text="Cancelados", 
                       variable=self.filter_cancelados).grid(row=1, column=0, sticky='w', pady=2)
        ttk.Checkbutton(filter_frame, text="Devueltos/Reembolsados", 
                       variable=self.filter_devueltos).grid(row=2, column=0, sticky='w', pady=2)
        ttk.Checkbutton(filter_frame, text="Demorados", 
                       variable=self.filter_demorados).grid(row=3, column=0, sticky='w', pady=2)

        lf = ttk.LabelFrame(main, text="Archivo HTML", padding=10)
        lf.grid(row=4, column=0, columnspan=2, sticky='ew', pady=(0, 20))

        ttk.Label(lf, textvariable=self.file_path, width=50).grid(row=0, column=0, padx=(0, 10))
        ttk.Button(lf, text="Seleccionar Archivo", command=self._select_file).grid(row=0, column=1)
        
        # Frame para descarga automática
        auto_frame = ttk.LabelFrame(main, text="Descarga Automática", padding=10)
        auto_frame.grid(row=5, column=0, columnspan=2, sticky='ew', pady=(0, 15))
        
        ttk.Label(auto_frame, text="🎯 Detector G+H ACTIVO", 
                 font=("Arial", 10, "bold")).grid(row=0, column=0)
        
        # Variable para el archivo descargado automáticamente
        self.auto_downloaded_file = None

        self.btn_process = ttk.Button(main, text="Procesar y Generar Archivos",
                                      command=self._process, state=tk.DISABLED)
        self.btn_process.grid(row=6, column=0, columnspan=2, pady=20)

        self.progress = ttk.Progressbar(main, variable=self.progress_var,
                                        maximum=100, mode='indeterminate')
        self.progress.grid(row=7, column=0, columnspan=2, sticky='ew', pady=(0, 10))

        sf = ttk.LabelFrame(main, text="Estado", padding=10)
        sf.grid(row=8, column=0, columnspan=2, sticky='nsew')
        ttk.Label(sf, textvariable=self.status_text, font=('Helvetica', 9),
                  wraplength=600).grid(row=0, column=0, sticky='ew')

    def _select_file(self):
        filename = filedialog.askopenfilename(
            title="Seleccionar archivo HTML de Mercado Libre",
            filetypes=[("Archivos HTML", "*.html *.htm"), ("Todos los archivos", "*.*")],
            initialdir=Path.home() / 'Downloads'
        )
        if filename:
            self.selected_file = Path(filename)
            self.file_path.set(self.selected_file.name)
            self.btn_process['state'] = tk.NORMAL
            self.status_text.set("Archivo seleccionado. Listo para procesar.")
    
    def _download_page(self):
        """Descargar página automáticamente"""
        self.status_text.set("🌐 Iniciando descarga automática...")
        self.progress.start()
        
        # Ejecutar en hilo separado
        thread = threading.Thread(target=self._download_worker)
        thread.daemon = True
        thread.start()
    
    def _start_key_detection(self):
        """Iniciar detección de teclas G+H"""
        try:
            # Mostrar instrucciones iniciales
            self.status_text.set("🎯 DETECTOR G+H ACTIVADO:\n1. Ve a tu navegador con ML abierto\n2. Navega a tus ventas/pedidos\n3. Presiona G y H JUNTAS en la página\n4. Se descargará automáticamente")
            
            # Iniciar progreso
            self.progress.start()
            
            # Crear detector y descargador
            self.key_detector = MLKeyDetector()
            self.auto_downloader = MLAutoDownloader()
            
            # Iniciar detección
            self.key_detector.start_detection(self._on_keys_detected)
            
        except Exception as e:
            self.status_text.set(f"❌ Error: {e}")
    
    def _on_keys_detected(self):
        """Callback cuando se detectan teclas G+H"""
        try:
            # Actualizar estado
            self.root.after(0, lambda: self.status_text.set("✅ G+H detectadas - Descargando página automáticamente..."))
            logging.info("🔄 Iniciando descarga automática...")
            
            # Descargar página automáticamente
            downloaded_file = self.auto_downloader.download_current_page()
            
            if downloaded_file:
                logging.info(f"✅ Archivo descargado: {downloaded_file}")
                # Procesar automáticamente
                self.root.after(0, lambda: self.status_text.set(f"💾 Página guardada en cache\nProcesando automáticamente..."))
                
                # Configurar archivo para procesamiento
                self.selected_file = downloaded_file
                self.file_path.set("Archivo temporal (cache)")
                
                # Procesar automáticamente
                self.root.after(1000, self._process_with_cleanup)  # Esperar 1 segundo
                
            else:
                logging.error("❌ No se pudo descargar el archivo")
                self.root.after(0, lambda: self.status_text.set("❌ Error descargando página - Usa modo manual"))
                
        except Exception as e:
            logging.error(f"❌ Error en callback: {e}")
            self.root.after(0, lambda: self.status_text.set(f"❌ Error: {e}"))
        finally:
            # Detener progreso
            self.root.after(0, lambda: self.progress.stop())
            
            # Detener detector
            if hasattr(self, 'key_detector'):
                self.key_detector.stop_detection()
    
    def _process_with_cleanup(self):
        """Procesar y limpiar cache después"""
        try:
            # Procesar normalmente
            self._process()
            
            # Limpiar cache después de un delay
            self.root.after(5000, self._cleanup_cache)  # 5 segundos después
            
        except Exception as e:
            logging.error(f"Error en procesamiento con cleanup: {e}")
    
    def _cleanup_cache(self):
        """Limpiar cache después del procesamiento"""
        if hasattr(self, 'auto_downloader'):
            self.auto_downloader.cleanup_after_processing()
            self.root.after(0, lambda: self.status_text.set("✅ PDF generado - Cache limpiado automáticamente"))
    
    def _download_success(self, filepath):
        """Descarga exitosa"""
        self.progress.stop()
        self.status_text.set(f"✅ Página descargada: {filepath.name}\nHaz clic en 'Procesar Automáticamente' para generar el PDF")
    
    def _download_error(self, error_msg):
        """Error en descarga"""
        self.progress.stop()
        self.status_text.set(f"❌ Error: {error_msg}")
        messagebox.showerror("Error", f"Error descargando página:\n{error_msg}")

    def _process(self):
        self.btn_process['state'] = tk.DISABLED
        self.progress.start(10)
        threading.Thread(target=self._worker).start()

    def _worker(self):
        try:
            self.status_text.set("Procesando archivo HTML con lógica temporal (fines de semana)...")
            extractor = MLProductExtractor()
            
            # Configurar filtros basado en la selección del usuario
            extractor.filter_states = []
            if self.filter_reprogramados.get():
                extractor.filter_states.extend([
                    'reprogramado',
                    'envío reprogramado',
                    'reprogramado por el comprador', 
                    'envío reprogramado por el comprador'
                ])
            if self.filter_cancelados.get():
                extractor.filter_states.extend(['cancelado', 'cancelada'])  # NUEVO: Incluir ambas variantes
            if self.filter_devueltos.get():
                extractor.filter_states.extend(['devuelto', 'reembolsado'])
            if self.filter_demorados.get():
                extractor.filter_states.extend(['está demorado', 'demorado'])
            
            # NUEVO: Agregar filtros de productos entregados (siempre activos)
            extractor.filter_states.extend([
                'comprador ausente',
                'entregado al conductor',
                'entregado',
                'fue entregado',
                'ya fue entregado',
                'producto entregado',
                'pedido entregado',
                'envío entregado',
                'entrega completada',
                'entrega finalizada',
            ])
            
            # NUEVO: Agregar filtros adicionales para otros estados
            extractor.filter_states.extend([
                # Cancelaciones
                'cancelado',
                'cancelada',
                'cancelación',
                'cancelacion',
                'cancelaste la venta',
                'cancelaste la venta',
                'cancelada por el comprador',
                'cancelado por el comprador',
                'venta cancelada',
                'venta cancelada por el comprador',
                'comprador canceló',
                'comprador cancelo',
                'comprador canceló la compra',
                'comprador cancelo la compra',
                
                # Reclamos
                'reclamo abierto',
                'reclamo cerrado',
                'reclamo en proceso',
                'reclamo resuelto',
                'reclamo pendiente',
                'reclamo iniciado',
                'reclamo finalizado',
                
                # En camino
                'en camino',
                'en tránsito',
                'en transito',
                'enviado',
                'despachado',
                'en ruta',
                'en distribución',
                'en distribucion',
                
                # Mediación
                'mediación',
                'mediacion',
                'mediación abierta',
                'mediacion abierta',
                'mediación cerrada',
                'mediacion cerrada',
                'mediación en proceso',
                'mediacion en proceso',
                'mediación finalizada',
                'mediacion finalizada',
                
                # Otros estados problemáticos
                'problema con el envío',
                'problema con el envio',
                'envío con problemas',
                'envio con problemas',
                'pendiente de resolución',
                'pendiente de resolucion',
                'en revisión',
                'en revision',
                'solicitud de devolución',
                'solicitud de devolucion',
                'devolución solicitada',
                'devolucion solicitada',
            ])
            
            logger.info(f"🎯 Filtros activos: {extractor.filter_states}")
            
            # Mostrar información del día y umbral
            current_day = datetime.now().strftime('%A')
            logger.info(f"🗓️  Procesando en: {current_day}")
            logger.info(f"🕐 Umbral temporal: {extractor.temporal_threshold.strftime('%A %d/%m/%Y %H:%M')}")
            
            if datetime.now().weekday() == 0:  # Lunes
                logger.info("📅 MODO LUNES ACTIVADO: Urgentes desde viernes 16:00")
            
            organized_products = extractor.process_html(self.selected_file)
            
            base = self.selected_file.stem
            outdir = Path.home() / 'Desktop'
            
            # Generar reporte de debug
            debug_path = outdir / f"{base}_debug_report_temporal.txt"
            self.status_text.set("Generando reporte detallado de extracción...")
            extractor.save_debug_report(debug_path)
            
            total_valid = (len(organized_products['urgent']) + 
                          len(organized_products['normal']) + 
                          len(organized_products['to_review']))
            
            if total_valid == 0:
                filtered_count = extractor.stats['filtered_out']
                if filtered_count > 0:
                    self._error(f"No se encontraron productos válidos después del filtrado.\n{filtered_count} productos fueron filtrados por estado.\n\nRevisa el reporte detallado en: {debug_path.name}")
                else:
                    self._error(f"No se encontraron productos en el archivo.\n\nRevisa el reporte detallado en: {debug_path.name}")
                return

            json_path = outdir / f"{base}_productos_organizados.json"
            pdf_path = outdir / f"{base}_productos_organizados.pdf"

            self.status_text.set(f"Generando JSON organizado por secciones...")
            extractor.save_json(json_path)

            self.status_text.set("Generando PDF con secciones: Urgentes | Normales | A Revisar...")
            pdfg = PDFGenerator(organized_products)
            pdfg.generate(pdf_path)

            self.root.after(0, self._done, organized_products, extractor.stats['filtered_out'], json_path, pdf_path, debug_path)
        except Exception as e:
            logger.exception("Error en procesamiento")
            self._error(str(e))

    def _done(self, organized_products, filtered_count, json_path, pdf_path, debug_path):
        self.progress.stop()
        self.progress_var.set(100)
        
        urgent_count = len(organized_products['urgent'])
        normal_count = len(organized_products['normal'])
        review_count = len(organized_products['to_review'])
        total_valid = urgent_count + normal_count + review_count
        
        self.status_text.set(f"✓ Completado: {total_valid} productos extraídos, {filtered_count} filtrados")
        
        msg = (f"¡Proceso completado con lógica temporal de fines de semana!\n\n"
               f"🗓️  DÍA: {datetime.now().strftime('%A %d/%m/%Y')}\n"
               f"🕐 UMBRAL: {datetime.now().strftime('%A') if datetime.now().weekday() != 0 else 'Viernes'} 16:00\n\n"
               f"📊 RESULTADOS:\n"
               f"🔴 Productos URGENTES: {urgent_count}\n"
               f"✅ Productos NORMALES: {normal_count}\n"
               f"🟡 Productos A REVISAR: {review_count}\n"
               f"🚫 Productos filtrados: {filtered_count}\n\n"
               f"📁 Archivos generados:\n"
               f"📋 JSON organizado: {json_path.name}\n"
               f"📄 PDF con secciones: {pdf_path.name}\n"
               f"🔍 Reporte debug: {debug_path.name}\n"
               f"Ubicación: Escritorio\n\n¿Abrir el PDF?")
        
        # Abrir PDF automáticamente
        try:
            os.startfile(pdf_path)
        except Exception:
            try:
                os.system(f'open "{pdf_path}"')
            except Exception:
                os.system(f'xdg-open "{pdf_path}"')
        
        # Limpiar cache inmediatamente después de generar el PDF
        if hasattr(self, 'auto_downloader'):
            self.auto_downloader.cleanup_after_processing()
        
        # Cerrar el programa después de 3 segundos
        self.root.after(3000, self.root.destroy)

    def _error(self, message):
        self.progress.stop()
        self.progress_var.set(0)
        self.status_text.set(f"✗ Error: {message}")
        messagebox.showerror("Error", f"Ocurrió un error:\n\n{message}\n\nRevisá el log para más detalles.")
        self.btn_process['state'] = tk.NORMAL

    def run(self):
        # Configurar cierre limpio
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        
        # Activar automáticamente el detector G+H al iniciar
        self._start_key_detection()
        
        self.root.mainloop()
    
    def _on_closing(self):
        """Cerrar limpiamente al salir"""
        # Detener detector si está activo
        if hasattr(self, 'key_detector'):
            self.key_detector.stop_detection()
        
        # Limpiar cache al salir
        if hasattr(self, 'auto_downloader'):
            self.auto_downloader.cleanup_after_processing()
        
        self.root.destroy()


# ===================== CLI ACTUALIZADO ===================== #
def parse_args():
    p = argparse.ArgumentParser(description="Extractor ML HTML -> JSON + PDF CON FILTROS Y LÓGICA TEMPORAL")
    p.add_argument('html', nargs='?', help='Ruta al archivo HTML (si se omite se abre GUI)')
    p.add_argument('--outdir', help='Carpeta de salida (por defecto Escritorio)')
    p.add_argument('--open-pdf', action='store_true', help='Abrir el PDF al terminar')
    p.add_argument('--gui', action='store_true', help='Forzar interfaz gráfica')
    p.add_argument('--no-filter', action='store_true', help='Deshabilitar todos los filtros')
    return p.parse_args()


def cli_flow(args):
    html_path = Path(args.html)
    if not html_path.exists():
        logger.error(f"No existe el archivo: {html_path}")
        sys.exit(1)

    extractor = MLProductExtractor()
    
    # Los filtros están habilitados por defecto en CLI, salvo que se use --no-filter
    if args.no_filter:
        extractor.filter_states = []
        logger.info("🚫 Filtros deshabilitados por --no-filter")
    else:
        logger.info(f"🎯 Filtros activos: {extractor.filter_states}")
    
    logger.info(f"🗓️  Procesando en: {datetime.now().strftime('%A %d/%m/%Y')}")
    logger.info(f"🕐 Umbral temporal: {extractor.temporal_threshold.strftime('%A %d/%m/%Y %H:%M')}")
    
    if datetime.now().weekday() == 0:  # Lunes
        logger.info("📅 MODO LUNES ACTIVADO: Urgentes desde viernes 16:00")
    else:
        logger.info("📅 MODO NORMAL: Urgentes desde ayer 16:00")
    
    organized_products = extractor.process_html(html_path)
    
    outdir = Path(args.outdir) if args.outdir else Path.home() / 'Desktop'
    outdir.mkdir(parents=True, exist_ok=True)
    base = html_path.stem
    suffix = "_productos_organizados" if not args.no_filter else "_productos"
    
    # Generar reporte de debug
    debug_path = outdir / f"{base}_debug_report_temporal.txt"
    extractor.save_debug_report(debug_path)
    
    total_valid = (len(organized_products['urgent']) + 
                   len(organized_products['normal']) + 
                   len(organized_products['to_review']))
    
    if total_valid == 0:
        filtered_count = extractor.stats['filtered_out']
        if filtered_count > 0:
            logger.error(f"No se encontraron productos válidos después del filtrado. {filtered_count} productos fueron filtrados.")
            logger.error(f"Revisa el reporte detallado en: {debug_path}")
        else:
            logger.error("No se encontraron productos válidos.")
            logger.error(f"Revisa el reporte detallado en: {debug_path}")
        sys.exit(1)

    json_path = outdir / f"{base}{suffix}.json"
    pdf_path = outdir / f"{base}{suffix}.pdf"

    extractor.save_json(json_path)
    pdfg = PDFGenerator(organized_products)
    pdfg.generate(pdf_path)

    logger.info(f"🔴 Productos urgentes: {len(organized_products['urgent'])}")
    logger.info(f"✅ Productos normales: {len(organized_products['normal'])}")
    logger.info(f"🟡 Productos a revisar: {len(organized_products['to_review'])}")
    logger.info(f"🚫 Filtrados: {extractor.stats['filtered_out']}")
    logger.info(f"📋 Reporte detallado generado: {debug_path}")

    if args.open_pdf:
        try:
            os.startfile(pdf_path)
        except Exception:
            try:
                os.system(f'open "{pdf_path}"')
            except Exception:
                os.system(f'xdg-open "{pdf_path}"')

    logger.info("🎉 Proceso completado con lógica temporal.")


def main():
    args = parse_args()
    if args.gui or not args.html:
        try:
            MLExtractorGUI(args).run()
        except Exception as e:
            logger.exception("Error fatal en GUI")
            messagebox.showerror("Error Fatal", str(e))
            sys.exit(1)
    else:
        cli_flow(args)


if __name__ == '__main__':
    main()