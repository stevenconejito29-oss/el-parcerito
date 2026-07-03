#!/usr/bin/env python3
"""
SCRIPT DE DIAGNÓSTICO RÁPIDO - Sistema El Parcerito
Ejecutar: python3 diagnostico.py

Este script analiza el estado actual del sistema y reporta problemas.
"""

import os
import sys
import json
from datetime import datetime
from pathlib import Path

# Colors para terminal
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
END = '\033[0m'
BOLD = '\033[1m'

def print_header(title):
    print(f"\n{BOLD}{BLUE}{'='*60}{END}")
    print(f"{BOLD}{BLUE}{title}{END}")
    print(f"{BOLD}{BLUE}{'='*60}{END}")

def print_ok(msg):
    print(f"{GREEN}✓ {msg}{END}")

def print_error(msg):
    print(f"{RED}✗ {msg}{END}")

def print_warning(msg):
    print(f"{YELLOW}⚠ {msg}{END}")

def print_info(msg):
    print(f"{BLUE}ℹ {msg}{END}")

def check_file_exists(path, name):
    if Path(path).exists():
        print_ok(f"{name} existe")
        return True
    else:
        print_error(f"{name} FALTA: {path}")
        return False

def check_project_structure():
    print_header("1. ESTRUCTURA DEL PROYECTO")
    
    base = Path('/home/panzeta/Documentos/el-parcerito')
    
    files_to_check = [
        ('oxidian/app.py', 'App principal Flask'),
        ('oxidian/models.py', 'Modelos de BD'),
        ('oxidian/routes/public.py', 'Rutas públicas'),
        ('oxidian/routes/admin.py', 'Rutas admin'),
        ('oxidian/routes/superadmin.py', 'Rutas superadmin'),
        ('oxidian/services.py', 'Servicios'),
        ('chat/bot.js', 'Bot WhatsApp'),
        ('oxidian/static/js/carrito.js', 'JS Carrito'),
        ('oxidian/templates/base.html', 'Template base'),
    ]
    
    found = 0
    for file_path, name in files_to_check:
        full_path = base / file_path
        if check_file_exists(full_path, name):
            found += 1
    
    print_info(f"Encontrados {found}/{len(files_to_check)} archivos críticos")
    return found == len(files_to_check)

def check_database_config():
    print_header("2. CONFIGURACIÓN DE BASE DE DATOS")
    
    env_file = Path('/home/panzeta/Documentos/el-parcerito/.env')
    
    if not env_file.exists():
        print_error(f".env no encontrado en {env_file.parent}")
        return False
    
    print_ok(".env existe")
    
    # Leer .env (básicamente)
    try:
        with open(env_file) as f:
            env_content = f.read()
        
        checks = [
            ('DATABASE_URL' in env_content, 'DATABASE_URL configurada'),
            ('OXIDIAN_KEY' in env_content, 'OXIDIAN_KEY configurada'),
            ('BOT_PANEL_KEY' in env_content, 'BOT_PANEL_KEY configurada'),
            ('EVOLUTION_API_KEY' in env_content, 'EVOLUTION_API_KEY configurada'),
        ]
        
        for check, label in checks:
            if check:
                print_ok(label)
            else:
                print_warning(label)
        
        return True
    except Exception as e:
        print_error(f"Error leyendo .env: {e}")
        return False

def check_python_deps():
    print_header("3. DEPENDENCIAS PYTHON")
    
    required = [
        'flask',
        'flask_sqlalchemy',
        'flask_login',
        'psycopg2',
        'requests',
        'python_dotenv',
    ]
    
    found = 0
    for package in required:
        try:
            __import__(package.replace('_', '-'))
            print_ok(f"{package} instalado")
            found += 1
        except ImportError:
            print_error(f"{package} NO INSTALADO")
    
    return found == len(required)

def check_node_deps():
    print_header("4. DEPENDENCIAS NODE.JS (Bot)")
    
    package_json = Path('/home/panzeta/Documentos/el-parcerito/chat/package.json')
    
    if not package_json.exists():
        print_error("package.json del bot no existe")
        return False
    
    print_ok("package.json existe")
    
    try:
        with open(package_json) as f:
            pkg = json.load(f)
        
        deps = pkg.get('dependencies', {})
        required = ['express', 'better-sqlite3', 'dotenv']
        
        found = 0
        for dep in required:
            if dep in deps:
                print_ok(f"{dep} en package.json")
                found += 1
            else:
                print_error(f"{dep} FALTA en package.json")
        
        return found == len(required)
    except Exception as e:
        print_error(f"Error leyendo package.json: {e}")
        return False

def check_file_sizes():
    print_header("5. TAMAÑO DE ARCHIVOS CRÍTICOS")
    
    files = [
        '/home/panzeta/Documentos/el-parcerito/oxidian/models.py',
        '/home/panzeta/Documentos/el-parcerito/oxidian/routes/public.py',
        '/home/panzeta/Documentos/el-parcerito/chat/bot.js',
    ]
    
    for file_path in files:
        path = Path(file_path)
        if path.exists():
            size_kb = path.stat().st_size / 1024
            size_mb = size_kb / 1024
            
            if size_mb > 1:
                print_warning(f"{path.name}: {size_mb:.2f} MB (grande, revisar si necesita refactor)")
            else:
                print_ok(f"{path.name}: {size_kb:.1f} KB")
        else:
            print_error(f"{path.name}: NO ENCONTRADO")

def check_code_issues():
    print_header("6. PROBLEMAS DE CÓDIGO (Análisis básico)")
    
    issues = []
    
    # Revisar models.py
    models_path = Path('/home/panzeta/Documentos/el-parcerito/oxidian/models.py')
    if models_path.exists():
        with open(models_path) as f:
            content = f.read()
        
        # Buscar anti-patrones
        if 'TODO' in content:
            todos = content.count('TODO')
            issues.append(f"models.py: {todos} TODOs sin resolver")
        
        if 'FIXME' in content:
            fixmes = content.count('FIXME')
            issues.append(f"models.py: {fixmes} FIXMEs sin resolver")
        
        if 'hardcoded' in content.lower():
            issues.append("models.py: Posibles valores hardcodeados detectados")
    
    # Revisar bot.js
    bot_path = Path('/home/panzeta/Documentos/el-parcerito/chat/bot.js')
    if bot_path.exists():
        with open(bot_path) as f:
            content = f.read()
        
        if 'TODO' in content:
            todos = content.count('TODO')
            issues.append(f"bot.js: {todos} TODOs sin resolver")
        
        if 'console.log' in content:
            logs = content.count('console.log')
            if logs > 20:
                issues.append(f"bot.js: {logs} console.logs (mucho debug)")
    
    if issues:
        print_warning("Posibles problemas encontrados:")
        for issue in issues:
            print(f"  • {issue}")
    else:
        print_ok("No se encontraron issues obvios")

def check_database_schema():
    print_header("7. ESQUEMA BASE DE DATOS")
    
    print_info("Para revisar el esquema:")
    print("  1. python -m flask shell")
    print("  2. from models import *")
    print("  3. db.metadata.tables.keys() → Ver todas las tablas")
    print("  4. db.inspect(Product).columns")
    print("\nTablas esperadas:")
    
    expected_tables = [
        'users', 'products', 'stock', 'orders', 'order_items',
        'combos', 'combo_groups', 'combo_items',
        'proveedores', 'proveedor_productos',
        'site_config', 'audit_log'
    ]
    
    for table in expected_tables:
        print(f"  □ {table}")

def check_product_quality():
    print_header("8. CALIDAD DE DATOS (Productos)")
    
    print_info("Para revisar datos de productos:")
    print("  python -m flask shell")
    print("  from models import Product")
    print("  ")
    print("  # Contar productos")
    print("  Product.query.count()")
    print("  ")
    print("  # Contar combos")
    print("  Product.query.filter_by(es_combo=True).count()")
    print("  ")
    print("  # Combos sin grupos (ERROR)")
    print("  [p.id for p in Product.query.filter_by(es_combo=True).all()")
    print("   if not p.combo_groups.count()]")
    print("  ")
    print("  # Productos sin precio")
    print("  Product.query.filter(Product.precio == None).count()")

def check_api_endpoints():
    print_header("9. ENDPOINTS API PRINCIPALES")
    
    print_info("Para testear endpoints:")
    print("\n# Catálogo")
    print("  curl http://localhost:5000/catalogo")
    print("\n# Crear pedido")
    print("  curl -X POST http://localhost:5000/checkout/crear \\")
    print("    -H 'Content-Type: application/x-www-form-urlencoded' \\")
    print("    -d 'telefono=1234567890&direccion=Calle+1'")
    print("\n# Validar modalidad")
    print("  curl -X POST http://localhost:5000/api/checkout/validar-modalidad \\")
    print("    -H 'Content-Type: application/json' \\")
    print("    -d '{\"carrito\":{\"1\":1},\"tipo_entrega\":\"delivery\"}'")

def check_chatbot_status():
    print_header("10. ESTADO CHATBOT")
    
    print_info("Para revisar bot:")
    print("\n# Ver logs")
    print("  tail -f /home/panzeta/Documentos/el-parcerito/chat/logs/* 2>/dev/null")
    print("\n# Verificar proceso Node.js")
    print("  ps aux | grep 'node.*bot.js'")
    print("\n# Conectar a BD local bot (SQLite)")
    print("  sqlite3 /home/panzeta/Documentos/el-parcerito/chat/bot_data.db")
    print("  .tables")

def generate_report():
    print_header("REPORTE DE DIAGNÓSTICO - El Parcerito")
    print(f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    checks = [
        ("Estructura del proyecto", check_project_structure()),
        ("Configuración BD", check_database_config()),
        ("Dependencias Python", check_python_deps()),
        ("Dependencias Node.js", check_node_deps()),
        ("Tamaño de archivos", check_file_sizes),
        ("Análisis de código", check_code_issues),
        ("Esquema BD", check_database_schema),
        ("Calidad de datos", check_product_quality),
        ("Endpoints API", check_api_endpoints),
        ("Estado Chatbot", check_chatbot_status),
    ]
    
    for name, check_func in checks:
        try:
            if callable(check_func):
                check_func()
            else:
                # Ya ejecutado, solo mostrar
                pass
        except Exception as e:
            print_error(f"Error en {name}: {e}")

def main():
    print(f"\n{BOLD}{BLUE}{'='*60}{END}")
    print(f"{BOLD}{BLUE}DIAGNÓSTICO DEL SISTEMA EL PARCERITO{END}")
    print(f"{BOLD}{BLUE}{'='*60}{END}")
    
    generate_report()
    
    print_header("PRÓXIMOS PASOS")
    print("""
1. ✅ Revisar los problemas encontrados arriba
2. ✅ Ejecutar comandos sugeridos en FLASK SHELL
3. ✅ Verificar que la BD tiene datos válidos
4. ✅ Testear endpoints con curl
5. ✅ Revisar logs si hay errores
6. ✅ Implementar Fase 1 del plan de mejoras

Para más detalles: Ver PLAN_MEJORAS_SISTEMA_COMPLETO.md
    """)
    
    print_header("CONTACTO")
    print("Si encuentras errores que no puedas resolver:")
    print("  1. Guarda el output de este diagnóstico")
    print("  2. Incluye stack trace de los errores")
    print("  3. Contacta al equipo de desarrollo")

if __name__ == '__main__':
    main()
