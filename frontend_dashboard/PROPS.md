# Apartado "Props" en el menu del SPA

El backend ya publica `/props` (pagina suelta) y `/api/props` (JSON).
Para que aparezca como una entrada mas del menu lateral (Dashboard, Chat AI,
TV, Compra y canje, Estadisticas...) hay que tocar el SPA
`threesixtybets-chat`, que NO esta en este repositorio.

El menu del SPA es por **estado**, no por rutas: cada entrada es
`{page, label, icon}` y la pagina se pinta con `page === "..."` dentro de
`<main>`. Nada de react-router.

## Paso 1 - copiar la pagina

```
frontend_dashboard/Props.jsx  ->  threesixtybets-chat/src/pages/Props.jsx
frontend_dashboard/Props.css  ->  threesixtybets-chat/src/pages/Props.css
```

## Paso 2 - importar

Junto a los demas imports de paginas (Dashboard, Chat, TV...):

```jsx
import Props from "./pages/Props";
```

## Paso 3 - anadir la entrada al menu

En el componente del sidebar, en el array de items:

```jsx
const items = [
  { page: "dashboard", label: "Dashboard", icon: "dashboard" },
  { page: "chat", label: "Chat AI", icon: "chat" },
  { page: "tv", label: "TV", icon: "tv" },
  { page: "credits", label: "Compra y canje", icon: "credits" },
  { page: "stats", label: "Estadisticas", icon: "stats" },
  { page: "props", label: "Props", icon: "props" },        // <-- NUEVO
  { page: "transactions", label: "Historial de transacciones", icon: "transactions" },
];
```

## Paso 4 - el icono

El componente de iconos es una cadena de comparaciones que termina en el de
`chat` como caso por defecto. Anade esta rama ANTES del `return` final
(persona + barras = props de jugador, mismo trazo que los demas iconos):

```jsx
if (type === "props") {
  return (
    <svg {...base}>
      <circle cx="9" cy="6" r="3" />
      <path d="M3 21v-2a6 6 0 0 1 6-6h2" />
      <path d="M15 21v-4" />
      <path d="M19 21v-8" />
      <path d="M23 21v-6" />
    </svg>
  );
}
```

## Paso 5 - pintar la pagina

Dentro de `<main>`, junto a las otras paginas:

```jsx
{page === "stats" && !premiumVencido(user) && <Stats />}
{page === "props" && <Props />}                            {/* <-- NUEVO */}
```

Si quieres que solo la vean los premium (como Estadisticas), usa el mismo
gate: `{page === "props" && !premiumVencido(user) && <Props />}`.

## Requisitos

- El backend debe tener desplegado `GET /api/props` (commit `95eca51`).
- `VITE_API_URL` del SPA tiene que apuntar a ese backend.
- Los datos los rellena `props_scheduler.py` cada 90 minutos; la primera
  visita antes del primer ciclo calcula al vuelo (unos segundos).

## Mientras no se toque el SPA

La pagina ya funciona abierta directamente:

```
https://<tu-servicio>.northflank.app/props
```
