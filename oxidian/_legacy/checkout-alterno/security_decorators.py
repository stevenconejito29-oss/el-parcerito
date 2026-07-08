"""
DECORADORES Y SEGURIDAD - Sistema El Parcerito
================================================

Módulo de decoradores para proteger rutas y ejecutar validaciones comunes.
"""

from functools import wraps
from flask import abort, jsonify, request, current_app
from flask_login import current_user
import logging
from datetime import datetime
import json

from services_core import RoleService, ConfigService, ValidationService, ResultadoOperacion

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# DECORADORES DE AUTENTICACIÓN Y AUTORIZACIÓN
# ═══════════════════════════════════════════════════════════════════

def login_requerido_staff(f):
    """Requiere que usuario esté autenticado y sea staff (no cliente)"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        
        if current_user.rol == 'cliente':
            abort(403)  # Clientes no son staff
        
        return f(*args, **kwargs)
    
    return wrapper


def requiere_rol(rol_requerido):
    """Requiere rol específico"""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            
            if current_user.rol != rol_requerido:
                logger.warning(
                    f"Acceso denegado: {current_user.email} intenta "
                    f"acceder como {rol_requerido}, pero es {current_user.rol}"
                )
                abort(403)
            
            return f(*args, **kwargs)
        
        return wrapper
    return decorator


def requiere_permisos(*permisos):
    """Requiere uno o varios permisos específicos"""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            
            # Verificar si usuario tiene alguno de los permisos
            tiene_permiso = any(
                RoleService.tiene_permiso(current_user, perm)
                for perm in permisos
            )
            
            if not tiene_permiso:
                logger.warning(
                    f"Permiso denegado: {current_user.email} intenta "
                    f"acceder sin permisos {permisos}"
                )
                abort(403)
            
            return f(*args, **kwargs)
        
        return wrapper
    return decorator


def requiere_rol_minimo(nivel_minimo: int):
    """Requiere rol con jerarquía mínima"""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            
            # Obtener nivel del rol del usuario
            from services_core import RoleEnum
            try:
                rol_enum = RoleEnum(current_user.rol)
                if rol_enum.nivel < nivel_minimo:
                    abort(403)
            except ValueError:
                abort(403)
            
            return f(*args, **kwargs)
        
        return wrapper
    return decorator


# ═══════════════════════════════════════════════════════════════════
# DECORADORES DE VALIDACIÓN
# ═══════════════════════════════════════════════════════════════════

def json_requerido(f):
    """Requiere que request sea JSON válido"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not request.is_json:
            return jsonify({'ok': False, 'error': 'Content-Type debe ser application/json'}), 400
        
        try:
            data = request.get_json()
        except Exception as e:
            return jsonify({'ok': False, 'error': f'JSON inválido: {str(e)}'}), 400
        
        # Pasar data en kwargs
        kwargs['data'] = data
        return f(*args, **kwargs)
    
    return wrapper


def validar_parametros(*parametros_requeridos):
    """Valida que los parámetros requeridos estén presentes en JSON"""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            data = kwargs.get('data', request.get_json() or {})
            
            faltantes = [p for p in parametros_requeridos if p not in data]
            if faltantes:
                return jsonify({
                    'ok': False,
                    'error': f'Parámetros faltantes: {", ".join(faltantes)}'
                }), 400
            
            return f(*args, **kwargs)
        
        return wrapper
    return decorator


def limitar_por_usuario(limite_por_hora: int = 100):
    """Rate limiting por usuario (basado en IP si no autenticado)"""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            from flask import session
            from datetime import datetime, timedelta
            
            # Identificador
            if current_user.is_authenticated:
                identificador = f"user_{current_user.id}"
            else:
                identificador = f"ip_{request.remote_addr}"
            
            # Clave de sesión
            clave = f"rate_limit_{identificador}_{f.__name__}"
            
            # Datos de rate limit
            if clave not in session:
                session[clave] = {"count": 0, "reset_at": datetime.now() + timedelta(hours=1)}
            
            datos = session[clave]
            
            # Resetear si pasó la hora
            if datetime.now() > datos["reset_at"]:
                datos["count"] = 0
                datos["reset_at"] = datetime.now() + timedelta(hours=1)
                session[clave] = datos
            
            # Incrementar contador
            datos["count"] += 1
            
            if datos["count"] > limite_por_hora:
                logger.warning(f"Rate limit excedido para {identificador}")
                return jsonify({
                    'ok': False,
                    'error': f'Demasiadas solicitudes. Límite: {limite_por_hora}/hora'
                }), 429
            
            session[clave] = datos
            return f(*args, **kwargs)
        
        return wrapper
    return decorator


# ═══════════════════════════════════════════════════════════════════
# DECORADORES DE AUDITORÍA Y LOGGING
# ═══════════════════════════════════════════════════════════════════

def auditar_accion(tipo_accion: str, detalles_fn=None):
    """
    Registra acciones en audit log
    
    Args:
        tipo_accion: tipo de acción ("crear", "modificar", "eliminar", etc)
        detalles_fn: función que extrae detalles de los kwargs
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            resultado = f(*args, **kwargs)
            
            try:
                if current_user.is_authenticated:
                    # Extraer detalles
                    detalles = {}
                    if detalles_fn:
                        detalles = detalles_fn(kwargs)
                    
                    # Registrar auditoría
                    from models import AuditLog
                    log = AuditLog(
                        usuario_id=current_user.id,
                        tipo_accion=tipo_accion,
                        descripcion=f"{current_user.nombre} - {f.__name__}",
                        detalles=json.dumps(detalles),
                        ip_address=request.remote_addr,
                        user_agent=request.headers.get('User-Agent', ''),
                        timestamp=datetime.now()
                    )
                    from extensions import db
                    db.session.add(log)
                    db.session.commit()
                    
                    logger.info(
                        f"[AUDIT] {current_user.email} - {tipo_accion} - "
                        f"{f.__name__}"
                    )
            except Exception as e:
                logger.error(f"Error auditando acción: {e}", exc_info=True)
            
            return resultado
        
        return wrapper
    return decorator


def log_endpoint(f):
    """Registra cada llamada a endpoint"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        usuario = current_user.email if current_user.is_authenticated else "Anonymous"
        logger.debug(
            f"[ENDPOINT] {request.method} {request.path} - Usuario: {usuario}"
        )
        
        try:
            resultado = f(*args, **kwargs)
            return resultado
        except Exception as e:
            logger.error(
                f"[ENDPOINT ERROR] {request.method} {request.path} - {e}",
                exc_info=True
            )
            raise
    
    return wrapper


# ═══════════════════════════════════════════════════════════════════
# DECORADORES DE RESPUESTA
# ═══════════════════════════════════════════════════════════════════

def respuesta_json(f):
    """Convierte ResultadoOperacion a respuesta JSON automáticamente"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        resultado = f(*args, **kwargs)
        
        # Si ya es una tupla (status code), devolver como está
        if isinstance(resultado, tuple):
            return resultado
        
        # Si es ResultadoOperacion, convertir a JSON
        if isinstance(resultado, ResultadoOperacion):
            status = 200 if resultado.exito else 400
            return jsonify(resultado.to_dict()), status
        
        # Devolver como está
        return resultado
    
    return wrapper


def cache_resultado(segundos: int = 300):
    """Cachea resultado por X segundos"""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            from flask import make_response
            
            resultado = f(*args, **kwargs)
            
            # Si es respuesta con headers, agregar cache
            if isinstance(resultado, tuple):
                response, status = resultado[0], resultado[1] if len(resultado) > 1 else 200
            else:
                response = resultado
                status = 200
            
            # Crear respuesta con headers de cache
            resp = make_response(response, status)
            resp.headers['Cache-Control'] = f'public, max-age={segundos}'
            
            return resp
        
        return wrapper
    return decorator


# ═══════════════════════════════════════════════════════════════════
# MANEJO DE ERRORES
# ═══════════════════════════════════════════════════════════════════

def manejar_errores_operacion(f):
    """Captura errores comunes y retorna respuesta amigable"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except ValueError as e:
            logger.error(f"ValueError en {f.__name__}: {e}")
            return jsonify({
                'ok': False,
                'error': f'Datos inválidos: {str(e)}',
                'codigo': 'VALOR_INVALID'
            }), 400
        except KeyError as e:
            logger.error(f"KeyError en {f.__name__}: {e}")
            return jsonify({
                'ok': False,
                'error': f'Parámetro faltante: {str(e)}',
                'codigo': 'PARAMETRO_FALTANTE'
            }), 400
        except Exception as e:
            logger.error(f"Error inesperado en {f.__name__}: {e}", exc_info=True)
            return jsonify({
                'ok': False,
                'error': 'Error interno del servidor',
                'codigo': 'SERVER_ERROR'
            }), 500
    
    return wrapper


# ═══════════════════════════════════════════════════════════════════
# HELPERS DE VALIDACIÓN Y RESPUESTA
# ═══════════════════════════════════════════════════════════════════

def respuesta_exitosa(data=None, mensaje: str = "OK", status: int = 200):
    """Helper para crear respuesta exitosa"""
    return jsonify({
        'ok': True,
        'data': data,
        'mensaje': mensaje
    }), status


def respuesta_error(error: str, codigo: str = "ERROR", status: int = 400):
    """Helper para crear respuesta de error"""
    return jsonify({
        'ok': False,
        'error': error,
        'codigo': codigo
    }), status


def respuesta_no_autenticado():
    """Helper para respuesta 401"""
    return respuesta_error("No autenticado", "NO_AUTENTICADO", 401)


def respuesta_no_autorizado():
    """Helper para respuesta 403"""
    return respuesta_error("No autorizado", "NO_AUTORIZADO", 403)


def respuesta_no_encontrado(recurso: str = "Recurso"):
    """Helper para respuesta 404"""
    return respuesta_error(f"{recurso} no encontrado", "NO_ENCONTRADO", 404)


if __name__ == "__main__":
    print("✓ Decoradores y Seguridad Cargados")
    print("  - Decoradores de Autenticación")
    print("  - Decoradores de Validación")
    print("  - Decoradores de Auditoría")
    print("  - Decoradores de Respuesta")
    print("  - Manejo de Errores")
