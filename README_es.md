# ArcShelf 🚀

[English Version](README.md)


Una aplicación de escritorio para gestionar, visualizar, buscar y etiquetar automáticamente colecciones de imágenes en Windows.

![Interfaz de ArcShelf con imágenes de ejemplo](readme_preview.png "Interfaz de ArcShelf con imágenes de ejemplo")

## Características Principales ✨

*   **Interfaz Gráfica Intuitiva:** Desarrollada con PyQt6 para una experiencia de usuario fluida.
*   **Galería de Imágenes Personalizable:** Ajusta la altura de las filas para adaptar la visualización a tu gusto.
*   **Previsualización Avanzada:** Visualiza imágenes con zoom y desplazamiento (paneo) integrados.
*   **Etiquetado Automático por IA:** Utiliza el potente modelo `wd-eva02-large-tagger-v3` para analizar y etiquetar tus imágenes automáticamente (ratings, personajes, etiquetas generales).
*   **Búsqueda Potente:**
    *   Busca por etiquetas usando operadores lógicos (`AND`, `OR`, `NOT`).
    *   Sugerencias de etiquetas mientras escribes.
    *   Búsqueda por similitud para encontrar imágenes visualmente parecidas.
*   **Gestión de Directorios:** Añade o elimina fácilmente las carpetas que contienen tus colecciones de imágenes.
*   **Detección de Duplicados:** Herramientas para identificar y gestionar imágenes duplicadas dentro de los directorios añadidos (accesible desde "Manage Directories...").
*   **Modo Presentación (Slideshow):** Visualiza tus imágenes en pantalla completa con transiciones automáticas.
*   **Amplio Soporte de Formatos:** Compatible con `.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`, `.gif`, `.tiff`, `.tif`.

## Requisitos 📋

*   **Sistema Operativo:** Windows (Probado en Windows 11 Pro).
*   **Python:** Versión 3.8 o superior. Se recomienda añadir Python al PATH del sistema.
*   **Hardware:**
    *   Se recomienda encarecidamente una **GPU NVIDIA** compatible con CUDA para obtener el mejor rendimiento en el etiquetado automático de imágenes.
    *   Si no se detecta una GPU compatible, la aplicación utilizará la **CPU** para el etiquetado, lo que resultará en un rendimiento significativamente menor para esa tarea.
*   **Dependencias:** No te preocupes por instalarlas manualmente. El script `run.bat` se encarga de todo. Las dependencias clave incluyen: `PyQt6`, `Pillow`, `numpy`, `onnxruntime-gpu`, `pandas`, `requests`.

## Instalación ⚙️

1.  **Clona el repositorio:** Abre una terminal (cmd, PowerShell, Git Bash) y ejecuta:
    ```bash
    git clone https://github.com/dmolmar/ArcShelf.git
    # Reemplaza la URL con la URL real de tu repositorio si es diferente
    ```
2.  **Navega al directorio:**
    ```bash
    cd ArcShelf
    ```
3.  **Ejecuta el script de configuración:** Simplemente haz doble clic en `run.bat` o ejecútalo desde la terminal:
    ```bash
    run.bat
    ```
    Este script hará lo siguiente automáticamente:
    *   Verificará si Python está instalado y accesible.
    *   Creará un entorno virtual aislado llamado `.venv` si no existe.
    *   Activará el entorno virtual.
    *   Instalará o actualizará todas las dependencias de Python listadas en `requirements.txt`, asegurándose de instalar la versión correcta de `onnxruntime` según tu hardware.
    *   Descargará los archivos necesarios para el modelo de IA (`model.onnx` y `selected_tags.csv`) desde Hugging Face si no se encuentran en la carpeta `models/`.

## Uso ▶️

1.  Una vez completada la instalación mediante `run.bat`, puedes iniciar la aplicación volviendo a ejecutar:
    ```bash
    run.bat
    ```
2.  **Primeros pasos:**
    *   Usa el botón **"Manage Directories..."** para añadir las carpetas que contienen tus imágenes. La aplicación las procesará para extraer metadatos y generar etiquetas (esto puede tardar un poco la primera vez, especialmente con colecciones grandes).
    *   Explora tu colección en la vista de galería.
    *   Haz clic en una imagen para verla en el panel de previsualización y consultar su información detallada y etiquetas en el panel de información.
    *   Utiliza la barra de búsqueda superior para encontrar imágenes por etiquetas. Prueba a escribir etiquetas y mira las sugerencias.

## Agradecimientos 🙏

El etiquetado automático de imágenes es posible gracias al modelo **wd-eva02-large-tagger-v3** creado por **SmilingWolf**. Puedes encontrar más información sobre el modelo en Hugging Face:
[https://huggingface.co/SmilingWolf/wd-eva02-large-tagger-v3](https://huggingface.co/SmilingWolf/wd-eva02-large-tagger-v3)