# Centroides municipales de Colombia

`municipios_colombia_centroides.json` es un snapshot local generado desde el
servicio oficial DANE `DIVIPOLA segun Marco Geoestadistico Nacional (MGN)`,
version 2024, capa `Municipio (317)`.

Fuente:

`https://geoportal.dane.gov.co/mparcgis/rest/services/Divipola/Serv_DIVIPOLA_MGN_2024/FeatureServer/317`

La consulta de generacion solicita todos los municipios, los campos DIVIPOLA,
`returnCentroid=true` y `outSR=4326`. El archivo contiene 1.121 registros con
codigos y claves `departamento + municipio` unicos. Las coordenadas se redondean
a seis decimales, que es la precision almacenada por `Empresa`.

El servicio Django nunca consulta DANE durante el runtime. Para actualizar el
snapshot se debe repetir la extraccion de forma controlada, verificar cantidad,
unicidad de codigos y claves, actualizar la metadata y ejecutar las pruebas de
`test_empresa_geografia`.

Las coordenadas explicitamente registradas en `Empresa` tienen prioridad sobre
este catálogo.
