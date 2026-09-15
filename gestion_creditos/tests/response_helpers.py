from collections import deque


def finalizar_respuesta_cliente(response):
    """Libera streaming sin saltarse closing_iterator_wrapper del TestClient.

    No llamar response.close(): request_finished puede cerrar la conexion del
    atomic de TestCase. Las respuestas no streaming ya las cierra el cliente.
    Solo para respuestas sincronas obtenidas con Django TestClient.
    """
    if response.streaming and not response.closed:
        deque(response.streaming_content, maxlen=0)
