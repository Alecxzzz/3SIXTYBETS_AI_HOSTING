---
name: flujo-desarrollo
description: Se activa cuando el usuario quiere desarrollar una nueva funcionalidad, endpoint o bugfix en el backend, guiándolo a través de las fases de Planear, Construir, Probar y Publicar.
user-invocable: true
---

# Flujo de Desarrollo Backend

## Instrucciones
Cuando el usuario solicite crear o modificar una funcionalidad, debes guiarlo estrictamente a través de los siguientes 4 pasos en orden. No pases al siguiente paso hasta que el usuario apruebe el actual.

### Pasos del Flujo:

1. 📋 PLANEAR
   - Analiza el requerimiento del usuario.
   - Diseña la arquitectura mentalmente: identifica qué controladores, modelos, rutas de API o servicios se verán afectados.
   - Presenta un resumen de los cambios propuestos y los archivos que vas a crear/modificar.
   - Detén la ejecución y pregunta: "¿Procedemos a construir con este plan?".

2. 🛠️ CONSTRUIR
   - Una vez aprobado el plan, genera el código limpio siguiendo las mejores prácticas del repositorio.
   - Asegúrate de modularizar el código, manejar errores correctamente y usar variables de entorno para datos sensibles.
   - Muestra los fragmentos de código claramente o aplícalos directamente si tienes las herramientas.

3. 🧪 PROBAR
   - Diseña la estrategia de pruebas para el código recién construido.
   - Escribe las pruebas unitarias o de integración necesarias (ej. usando Jest, Supertest o la suite del proyecto).
   - Si no hay entorno de pruebas automatizado, proporciona los comandos `curl` o payloads de Postman exactos para validar manualmente el nuevo endpoint.

4. 🚀 PUBLICAR
   - Prepara los pasos finales para llevar el código a producción o rama principal.
   - Genera una propuesta de mensaje de Commit semántico (ej. `feat(api): add new authentication endpoint`).
   - Lista los cambios realizados a modo de Pull Request (lista de control de cambios).
   - Revisa si es necesario actualizar la documentación de la API (como Swagger o el archivo README).
