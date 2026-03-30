# Perfiles y Accesos

## User
- Es la identidad base de Django.
- Todos los flujos parten de aqui.
- Puede existir sin password usable cuando nace por invitacion o carga controlada.

## PerfilUsuario
- Guarda datos adicionales del usuario final legacy.
- Hoy sigue siendo util para telefono/documento y tipologia general.

## ProductAccessProfile
- Bloquea el flujo principal del usuario final.
- Flujos actuales:
  - `LIBRANZA`
  - `EMPRENDIMIENTO`
  - `INVERSIONISTA`
  - `MARKETPLACE_BUYER`
- Se crea al primer login/registro valido del producto.

## PerfilPagador
- Representa al usuario corporativo que aprueba y paga por una empresa.
- Depende de `User` + `Empresa`.
- Habilita panel de pagador, carga de empleados, decision de solicitudes y pagos.

## PerfilEmpresaMarketing
- Representa al admin de una empresa dentro del marketplace.
- Depende de `User` + `Empresa`.
- Solo funciona para empresas que permiten marketplace.

## InvestorAccount
- Es la cuenta operativa del inversionista.
- Depende de `User`.
- Desde aqui cuelgan posiciones, cashflows y snapshots del dashboard.

## Tokens de acceso
- `PagadorAccessToken`: activacion y reset del pagador.
- `InvestorAccessToken`: activacion y reset del inversionista.
- Ambos se invalidan por uso o expiracion y permiten activar password sin crear usuarios duplicados.

## Regla practica
- Un `User` puede tener varios perfiles operativos internos.
- El acceso de producto del usuario final se controla con `ProductAccessProfile`.
- Los perfiles corporativos (`PerfilPagador`, `PerfilEmpresaMarketing`) no reemplazan el `User`; lo especializan.
