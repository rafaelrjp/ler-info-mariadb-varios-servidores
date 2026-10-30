"""Levantamento de bancos e tabelas de um servidor MySQL/MariaDB.

Usado por executar.py: analisar() imprime os resumos no terminal e grava
um CSV por banco com a lista de tabelas.
"""

import csv
import os
import re
import shutil
import socket
from decimal import Decimal
from itertools import groupby
from pathlib import Path

import pymysql

BANCOS_SISTEMA = ("information_schema", "mysql", "performance_schema", "sys")
HOSTS_LOCAIS = ("localhost", "127.0.0.1", "::1")


def conectar(cred):
    return pymysql.connect(
        host=cred["host"],
        port=int(cred.get("porta") or 3306),
        user=cred["usuario"],
        password=cred.get("senha", ""),
        charset="utf8mb4",
        connect_timeout=10,
    )


def consultar(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        colunas = [d[0] for d in cur.description]
        return colunas, cur.fetchall()


def nome_seguro(texto):
    # Remove caracteres não permitidos em nomes de arquivo no Windows
    return re.sub(r'[<>:"/\\|?*\s]', "_", texto)


def fmt(valor):
    if valor is None:
        return "NULL"
    if isinstance(valor, Decimal) and valor.as_tuple().exponent == 0:
        valor = int(valor)
    if isinstance(valor, (float, Decimal)):
        return f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if isinstance(valor, int):
        return f"{valor:,}".replace(",", ".")
    return str(valor)


def valor_csv(valor):
    # Vírgula decimal para abrir direto no Excel em pt-BR
    if valor is None:
        return ""
    if isinstance(valor, (float, Decimal)):
        return str(valor).replace(".", ",")
    return valor


def imprimir_tabela(titulo, colunas, linhas):
    print(f"\n=== {titulo} ===")
    if not linhas:
        print("(nenhum registro)")
        return
    texto = [[fmt(v) for v in linha] for linha in linhas]
    larguras = [max([len(c)] + [len(l[i]) for l in texto]) for i, c in enumerate(colunas)]
    sep = "+" + "+".join("-" * (w + 2) for w in larguras) + "+"
    print(sep)
    print("| " + " | ".join(c.ljust(w) for c, w in zip(colunas, larguras)) + " |")
    print(sep)
    for linha, original in zip(texto, linhas):
        celulas = [
            v.rjust(w) if isinstance(o, (int, float, Decimal)) else v.ljust(w)
            for v, w, o in zip(linha, larguras, original)
        ]
        print("| " + " | ".join(celulas) + " |")
    print(sep)


def tamanho_pasta(caminho):
    total = 0
    for raiz, _, arquivos in os.walk(caminho):
        for nome in arquivos:
            try:
                total += os.path.getsize(os.path.join(raiz, nome))
            except OSError:
                pass
    return total


def host_local(host):
    return host.lower() in HOSTS_LOCAIS or host.lower() == socket.gethostname().lower()


def imprimir_resumos(conn):
    imprimir_tabela("Resumo por banco", *consultar(conn, """
        SELECT IFNULL(table_schema, 'TOTAL') banco, COUNT(*) tabelas,
               SUM(table_rows) linhas_aprox,
               ROUND(SUM(data_length)/1048576, 2) dados_MB,
               ROUND(SUM(index_length)/1048576, 2) indices_MB,
               ROUND(SUM(data_length + index_length)/1048576, 2) total_MB,
               ROUND(SUM(data_free)/1048576, 2) livre_MB
        FROM information_schema.tables
        WHERE table_type = 'BASE TABLE'
        GROUP BY table_schema WITH ROLLUP"""))

    imprimir_tabela("Objetos por tipo", *consultar(conn, """
        SELECT table_schema banco, table_type tipo, COUNT(*) qtd
        FROM information_schema.tables
        GROUP BY table_schema, table_type
        ORDER BY table_schema, table_type"""))

    imprimir_tabela("Por engine", *consultar(conn, """
        SELECT engine, COUNT(*) tabelas,
               ROUND(SUM(data_length + index_length)/1073741824, 2) total_GB
        FROM information_schema.tables
        WHERE table_type = 'BASE TABLE'
        GROUP BY engine"""))

    imprimir_tabela("Tabelas vazias (table_rows = 0) por banco", *consultar(conn, """
        SELECT table_schema banco, CAST(SUM(table_rows = 0) AS UNSIGNED) vazias,
               COUNT(*) total
        FROM information_schema.tables
        WHERE table_type = 'BASE TABLE'
          AND table_schema NOT IN %s
        GROUP BY table_schema""", (BANCOS_SISTEMA,)))

    imprimir_tabela("Bancos sem nenhuma tabela", *consultar(conn, """
        SELECT s.schema_name banco
        FROM information_schema.schemata s
        LEFT JOIN information_schema.tables t ON t.table_schema = s.schema_name
        WHERE t.table_name IS NULL
        ORDER BY s.schema_name"""))

    imprimir_tabela("30 maiores tabelas", *consultar(conn, """
        SELECT table_schema banco, table_name tabela, engine,
               table_rows linhas_aprox,
               ROUND(data_length/1048576, 1) dados_MB,
               ROUND(index_length/1048576, 1) idx_MB,
               ROUND((data_length + index_length)/1048576, 1) total_MB
        FROM information_schema.tables
        WHERE table_type = 'BASE TABLE'
        ORDER BY data_length + index_length DESC
        LIMIT 30"""))


def imprimir_disco(datadir):
    pasta_dados = Path(datadir)
    por_banco = sorted(
        ((p.name, tamanho_pasta(p) / 1024**3) for p in pasta_dados.iterdir() if p.is_dir()),
        key=lambda x: x[1], reverse=True,
    )
    imprimir_tabela("Disco real por banco (datadir)", ["banco", "GB"], por_banco)
    print(f"Total datadir: {fmt(tamanho_pasta(pasta_dados) / 1024**3)} GB")

    arquivos = sorted(
        (p for p in pasta_dados.iterdir() if p.is_file()),
        key=lambda p: p.stat().st_size, reverse=True,
    )[:8]
    imprimir_tabela(
        "Maiores arquivos na raiz do datadir",
        ["arquivo", "MB"],
        [(p.name, p.stat().st_size / 1024**2) for p in arquivos],
    )

    disco = shutil.disk_usage(pasta_dados)
    imprimir_tabela(
        f"Unidade {pasta_dados.anchor}",
        ["usado_GB", "livre_GB", "total_GB"],
        [(disco.used / 1024**3, disco.free / 1024**3, disco.total / 1024**3)],
    )


def exportar_csvs(conn, pasta_saida, usuario, carimbo):
    """Grava {banco}_{usuario}_{data}_{hora}.csv para cada banco com tabelas."""
    colunas, linhas = consultar(conn, """
        SELECT table_schema banco, table_name tabela, table_type tipo, engine,
               row_format, table_rows linhas_aprox,
               ROUND(avg_row_length) bytes_media_linha,
               ROUND(data_length/1048576, 2) dados_MB,
               ROUND(index_length/1048576, 2) indices_MB,
               ROUND((data_length + index_length)/1048576, 2) total_MB,
               ROUND(data_free/1048576, 2) livre_MB,
               auto_increment, table_collation, create_time, update_time
        FROM information_schema.tables
        WHERE table_schema <> 'information_schema'
        ORDER BY table_schema, table_name""")

    gerados = []
    for banco, grupo in groupby(linhas, key=lambda l: l[0]):
        arquivo = pasta_saida / nome_seguro(f"{banco}_{usuario}_{carimbo}.csv")
        with open(arquivo, "w", newline="", encoding="utf-8-sig") as f:
            escritor = csv.writer(f, delimiter=";")
            escritor.writerow(colunas)
            escritor.writerows([valor_csv(v) for v in linha] for linha in grupo)
        gerados.append(arquivo)
    return gerados


def analisar(cred, pasta_saida, carimbo):
    """Analisa um servidor e retorna a lista de CSVs gerados."""
    conn = conectar(cred)
    try:
        _, [(versao, datadir, file_per_table)] = consultar(
            conn, "SELECT VERSION(), @@datadir, @@innodb_file_per_table"
        )
        imprimir_tabela(
            "Servidor",
            ["host", "usuario", "versao", "datadir", "file_per_table"],
            [(cred["host"], cred["usuario"], versao, datadir, file_per_table)],
        )

        imprimir_resumos(conn)

        # Tamanho real em disco só quando o servidor roda nesta máquina
        if host_local(cred["host"]) and Path(datadir).is_dir():
            imprimir_disco(datadir)
        else:
            print("\n(servidor remoto ou datadir inacessível: tamanho real em disco ignorado)")

        gerados = exportar_csvs(conn, pasta_saida, cred["usuario"], carimbo)
        print(f"\n{len(gerados)} CSVs gerados em {pasta_saida}")
        return gerados
    finally:
        conn.close()
