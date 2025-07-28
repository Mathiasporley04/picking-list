# FUNCIONALIDADES DEL PROGRAMA - Extractor ML Uruguay V10

## 🎯 **FUNCIÓN PRINCIPAL**
Extrae, organiza y genera reportes de productos de Mercado Libre Uruguay desde archivos HTML de ventas/pedidos.

## 📋 **PROCESO COMPLETO**

### 1️⃣ **ENTRADA DE DATOS**
- **Descarga automática:** Detector G+H para descarga automática de páginas
- **Archivo manual:** Selección de archivos HTML guardados manualmente
- **Compatibilidad:** Funciona con cualquier navegador (Chrome, Firefox, Edge)

### 2️⃣ **EXTRACCIÓN DE DATOS**
- **Productos:** Nombre, precio, cantidad, SKU, imagen, enlace
- **Estados:** Estado del pedido, fecha de acordado, ID de pedido
- **Clasificación:** Análisis temporal con lógica de fines de semana
- **Filtrado:** Eliminación automática de productos cancelados/entregados

### 3️⃣ **LÓGICA TEMPORAL INTELIGENTE**
- **LUNES:** Productos "a acordar" después del VIERNES 16:00 → URGENTES
- **MAR-VIE:** Productos "a acordar" después de AYER 16:00 → URGENTES
- **Productos antiguos:** "A acordar" anteriores al umbral → A REVISAR
- **Resto:** Productos normales

### 4️⃣ **ORGANIZACIÓN AUTOMÁTICA**
- **🔴 URGENTES:** Requieren atención inmediata
- **✅ NORMALES:** Productos estándar
- **🟡 A REVISAR:** Productos antiguos que necesitan verificación

### 5️⃣ **GENERACIÓN DE ARCHIVOS**
- **📄 PDF:** "Lista de Pedidos.pdf" en el escritorio
- **📋 JSON:** Datos organizados en directorio de cache
- **🔍 Reporte:** Análisis detallado en directorio de cache
- **📝 Log:** Registro de actividades en directorio de cache

## 🎨 **INTERFAZ GRÁFICA**

### **Diseño Limpio y Profesional**
- **Título:** "Extractor ML - FILTROS + LÓGICA TEMPORAL"
- **Instrucciones:** Guías claras para uso automático y manual
- **Estado en tiempo real:** Progreso y mensajes informativos
- **Botones intuitivos:** Seleccionar archivo, procesar, descarga automática

### **Detección Automática G+H**
- **Activación:** Automática al iniciar el programa
- **Instrucciones:** Presionar G y H juntas en la página de ML
- **Procesamiento:** Automático después de la descarga
- **Cierre:** Programa se cierra automáticamente al terminar

## 🔧 **FUNCIONES TÉCNICAS**

### **Gestión de Archivos**
- **Cache inteligente:** Directorio temporal para archivos intermedios
- **Limpieza automática:** Eliminación de archivos temporales
- **Compatibilidad universal:** Funciona en cualquier Windows
- **Soporte OneDrive:** Compatible con configuraciones empresariales

### **Detección de Directorios**
- **Escritorio:** Búsqueda inteligente en múltiples ubicaciones
- **Cache:** Directorio temporal con permisos de escritura
- **Fallbacks:** Descargas, Documentos, directorio actual
- **Creación automática:** Directorios se crean si no existen

### **Logging Avanzado**
- **Rastreo completo:** Todas las operaciones registradas
- **Debugging:** Información detallada para resolver problemas
- **Ubicación:** Log guardado en directorio de cache
- **Formato:** Timestamp, nivel, mensaje descriptivo

## 📊 **FILTROS AUTOMÁTICOS**

### **Estados Filtrados (No aparecen en el PDF)**
- **Cancelaciones:** Cancelado, cancelada, cancelación
- **Entregas:** Entregado, entregado al conductor, entrega completada
- **Reclamos:** Reclamo abierto, mediación, problema con envío
- **Reprogramaciones:** Reprogramado, envío reprogramado
- **Otros:** Devuelto, reembolsado, comprador ausente

### **Estados Temporales (Clasificación especial)**
- **"A acordar":** Clasificación según fecha y día de la semana
- **"En camino":** Productos en tránsito
- **"Pendiente":** Estados que requieren revisión

## 🎯 **SALIDAS DEL PROGRAMA**

### **PDF - Lista de Pedidos**
- **Secciones organizadas:** Urgentes, Normales, A Revisar
- **Información completa:** Imagen, precio, cantidad, SKU
- **Diseño profesional:** Optimizado para impresión térmica
- **Ubicación:** Escritorio del usuario

### **JSON - Datos Estructurados**
- **Organización:** Productos separados por prioridad
- **Metadatos:** Timestamp, filtros aplicados, estadísticas
- **Ubicación:** Directorio de cache
- **Formato:** JSON legible con indentación

### **Reporte - Análisis Detallado**
- **Estadísticas:** Productos procesados, filtrados, clasificados
- **Debugging:** Información de cada producto procesado
- **Razones:** Por qué cada producto fue filtrado o clasificado
- **Ubicación:** Directorio de cache

## 🚀 **CARACTERÍSTICAS AVANZADAS**

### **Compatibilidad Universal**
- **Windows 10/11:** Funciona en cualquier versión
- **OneDrive:** Soporte completo personal y empresarial
- **Usuarios:** Funciona con cualquier nombre de usuario
- **Permisos:** Manejo automático de permisos de escritura

### **Robustez**
- **Manejo de errores:** Recuperación automática de fallos
- **Verificación:** Comprobación de archivos y directorios
- **Fallbacks:** Múltiples opciones si algo falla
- **Logging:** Registro completo para debugging

### **Automatización**
- **Descarga automática:** Ctrl+S automático con ruta específica
- **Procesamiento:** Automático después de descarga
- **Limpieza:** Eliminación automática de archivos temporales
- **Cierre:** Programa se cierra automáticamente

## 📈 **BENEFICIOS DEL PROGRAMA**

### **Para el Usuario**
- **Ahorro de tiempo:** Automatización completa del proceso
- **Organización:** Productos clasificados por prioridad
- **Claridad:** PDF profesional y fácil de leer
- **Confiabilidad:** Funciona en cualquier computadora

### **Para el Negocio**
- **Eficiencia:** Procesamiento rápido de pedidos
- **Priorización:** Enfoque en productos urgentes
- **Trazabilidad:** Registro completo de operaciones
- **Escalabilidad:** Funciona con cualquier volumen de pedidos

## 🎉 **RESULTADO FINAL**
Un programa completo, profesional y confiable que transforma archivos HTML de Mercado Libre en listas de pedidos organizadas y listas para usar en operaciones comerciales. 