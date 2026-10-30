"""Roda analisa_mariadb.analisar() para cada servidor de servidores.csv.

Uso:
    python executar.py                  # usa servidores.csv desta pasta
    python executar.py outra_lista.csv

Saída em saida/{host}_{porta}/:
    {banco}_{usuario}_{data}_{hora}.csv    um por banco
    resumo_{usuario}_{data}_{hora}.txt     cópia do que foi impresso no terminal
"""

import csv
import sys
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

import analisa_mariadb

PASTA = Path(__file__).resolve().parent
ARQ_SERVIDORES = PASTA / "servidores.csv"
PASTA_SAIDA = PASTA / "saida"


class Duplicador:
    """Escreve ao mesmo tempo no terminal e no arquivo de resumo."""

    def __init__(self, *destinos):
        self.destinos = destinos

    def write(self, texto):
        for d in self.destinos:
            d.write(texto)

    def flush(self):
        for d in self.destinos:
            d.flush()


def ler_servidores(caminho):
    with open(caminho, newline="", encoding="utf-8-sig") as f:
        linhas = [l for l in f if l.strip() and not l.lstrip().startswith("#")]
    servidores = []
    for linha in csv.DictReader(linhas, delimiter=";"):
        s = {k.strip().lower(): (v or "").strip() for k, v in linha.items() if k}
        if s.get("host"):
            servidores.append(s)
    return servidores


def main():
    arq_servidores = Path(sys.argv[1]) if len(sys.argv) > 1 else ARQ_SERVIDORES
    servidores = ler_servidores(arq_servidores)
    if not servidores:
        print(f"Nenhum servidor em {arq_servidores}")
        return 1

    resultados = []
    for cred in servidores:
        porta = cred.get("porta") or "3306"
        carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
        pasta = PASTA_SAIDA / analisa_mariadb.nome_seguro(f"{cred['host']}_{porta}")
        pasta.mkdir(parents=True, exist_ok=True)
        arq_resumo = pasta / analisa_mariadb.nome_seguro(f"resumo_{cred['usuario']}_{carimbo}.txt")

        print(f"\n########## {cred['usuario']}@{cred['host']}:{porta} ##########")
        try:
            with open(arq_resumo, "w", encoding="utf-8") as f, \
                    redirect_stdout(Duplicador(sys.stdout, f)):
                gerados = analisa_mariadb.analisar(cred, pasta, carimbo)
            resultados.append((cred["host"], porta, cred["usuario"], "OK", len(gerados)))
        except Exception as erro:
            print(f"ERRO: {erro}")
            arq_resumo.write_text(f"ERRO: {erro}\n", encoding="utf-8")
            resultados.append((cred["host"], porta, cred["usuario"], f"ERRO: {erro}", 0))

    analisa_mariadb.imprimir_tabela(
        "Execução", ["host", "porta", "usuario", "status", "csvs"], resultados
    )
    print(f"Saída em: {PASTA_SAIDA}")
    return 0 if all(r[3] == "OK" for r in resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
