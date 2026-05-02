"""
Módulo de importação de dados para o SGB.

Suporta dois formatos:
  1. CSV exportado do Notion (colunas: Observações, Tags, Números, Datas)
  2. Texto livre do Bloco de Notas do iPhone
"""

import re
import csv
import io
from datetime import datetime
from decimal import Decimal
from database import Session, Feira, Compra, Cliente, HistoricoFiado, ExtraFeira


# ─────────────────────────────────────────────
# UTILITÁRIOS
# ─────────────────────────────────────────────

def _parse_valor(s: str) -> float:
    """Converte 'R$1.234,56' ou '-R$302,50' para float."""
    s = s.strip().replace('R$', '').replace(' ', '')
    negativo = s.startswith('-')
    s = s.lstrip('-').replace('.', '').replace(',', '.')
    try:
        v = float(s)
        return -v if negativo else v
    except ValueError:
        return 0.0


def _parse_peso_preco(obs: str):
    """
    Extrai lista de (peso, preco) de strings como:
      'Peso: 128.8(200@) + 122.5(180@)'
      'Peso: 90+112=202 (180@/200@)'
      'Peso: 152.8 (200@)'
    Retorna lista de dicts {'peso': float, 'preco': float}.
    """
    lotes = []
    obs = obs.strip()

    # Padrão: N.N(P@) + N.N(P@)  — cada lote com seu preço inline
    padrao_inline = re.findall(r'([\d.]+)\s*\(([\d]+)@\)', obs)
    if padrao_inline:
        for peso_s, preco_s in padrao_inline:
            lotes.append({'peso': float(peso_s), 'preco': float(preco_s)})
        return lotes

    # Padrão: Peso: NNN (P@) — único lote
    m = re.match(r'Peso:\s*([\d.]+)\s*\(([\d]+)@\)', obs)
    if m:
        lotes.append({'peso': float(m.group(1)), 'preco': float(m.group(2))})
        return lotes

    # Fallback: tenta extrair qualquer número como peso sem preço (marca 0)
    m2 = re.search(r'Peso:\s*([\d.]+)', obs)
    if m2:
        lotes.append({'peso': float(m2.group(1)), 'preco': 0.0})

    return lotes


def _data_str(dt: datetime) -> str:
    return dt.strftime('%d/%m/%Y')


def _feira_ja_existe(session, data: datetime) -> bool:
    return session.query(Feira).filter_by(data=data.date()).first() is not None


# ─────────────────────────────────────────────
# PARSER 1 — NOTION CSV
# ─────────────────────────────────────────────

def parsear_notion_csv(conteudo_bytes: bytes) -> list[dict]:
    """
    Recebe o conteúdo bruto do CSV exportado do Notion e retorna
    lista de feiras prontas para importação.

    Cada feira: {
        'data': str dd/mm/yyyy,
        'caixa_in': float,
        'caixa_out': float,
        'total_pix': float,
        'total_cartao': float,
        'lotes': [{'peso': float, 'preco': float}],
        'obs': str,
        'ja_existe': bool,
    }
    """
    texto = conteudo_bytes.decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(texto))

    # Agrupa linhas por data
    from collections import defaultdict
    by_date = defaultdict(list)
    for row in reader:
        data = row.get('Datas', '').strip()
        if data and re.match(r'\d{2}/\d{2}/\d{4}', data):
            by_date[data].append(row)

    feiras = []
    session = Session()
    try:
        for data_str, linhas in sorted(by_date.items()):
            try:
                data_dt = datetime.strptime(data_str, '%d/%m/%Y')
            except ValueError:
                continue

            caixa_in = 0.0
            caixa_out = 0.0
            total_pix = 0.0
            total_cartao = 0.0
            lotes = []
            obs_list = []

            for l in linhas:
                tag = l.get('Tags', '').strip()
                num_s = l.get('Números', '').strip()
                obs = l.get('Observações ', '').strip()
                valor = abs(_parse_valor(num_s)) if num_s else 0.0

                if tag == 'Caixa IN':
                    caixa_in += valor
                    if obs and not obs.startswith('Peso:'):
                        obs_list.append(obs)
                elif tag == 'Caixa OUT':
                    caixa_out += valor
                    if obs.startswith('Peso:'):
                        lotes.extend(_parse_peso_preco(obs))
                    elif obs:
                        obs_list.append(obs)
                elif tag == 'Pix':
                    total_pix += valor
                elif tag == 'Cartão':
                    total_cartao += valor

            if not lotes and caixa_in == 0 and caixa_out == 0:
                continue  # linha de divisória / totalizador

            feiras.append({
                'data': data_str,
                'caixa_in': round(caixa_in, 2),
                'caixa_out': round(caixa_out, 2),
                'total_pix': round(total_pix, 2),
                'total_cartao': round(total_cartao, 2),
                'lotes': lotes,
                'obs': ' | '.join(obs_list),
                'ja_existe': _feira_ja_existe(session, data_dt),
            })
    finally:
        session.close()

    return feiras


# ─────────────────────────────────────────────
# PARSER 2 — TEXTO DO BLOCO DE NOTAS
# ─────────────────────────────────────────────

def _extrair_bloco_data(bloco: str):
    """
    Tenta extrair a data de um bloco de texto.
    Aceita: 'Domingo 05 de abril 2026', '05/04/2026', '05-04-2026'
    """
    meses = {
        'janeiro': 1, 'fevereiro': 2, 'março': 3, 'marco': 3,
        'abril': 4, 'maio': 5, 'junho': 6, 'julho': 7,
        'agosto': 8, 'setembro': 9, 'outubro': 10,
        'novembro': 11, 'dezembro': 12,
    }
    # "Domingo 05 de abril 2026"
    m = re.search(r'(\d{1,2})\s+de\s+(\w+)\s+(\d{4})', bloco, re.IGNORECASE)
    if m:
        dia, mes_str, ano = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        mes = meses.get(mes_str)
        if mes:
            try:
                return datetime(ano, mes, dia)
            except ValueError:
                pass
    # "05/04/2026" ou "05-04-2026"
    m2 = re.search(r'(\d{2})[/-](\d{2})[/-](\d{4})', bloco)
    if m2:
        try:
            return datetime(int(m2.group(3)), int(m2.group(2)), int(m2.group(1)))
        except ValueError:
            pass
    return None


def _soma_expressao(s: str) -> float:
    """
    Resolve expressões como '267,00 + 341,00' ou '128,00 + 125,00'.
    """
    s = s.strip()
    partes = re.split(r'\+', s)
    total = 0.0
    for p in partes:
        p = p.strip().replace('.', '').replace(',', '.')
        try:
            total += float(p)
        except ValueError:
            pass
    return round(total, 2)


def _extrair_fiados_notas(obs_texto: str) -> list[dict]:
    """
    Tenta extrair movimentações de fiado das observações.
    Retorna lista de {'nome': str, 'tipo': 'DEBITO'|'CREDITO', 'valor': float, 'descricao': str}.
    Heurística: se menciona "pagou" → crédito; se menciona "fiado" sem "pagou" → débito.
    """
    fiados = []
    linhas = [l.strip() for l in obs_texto.split('\n') if l.strip()]
    for linha in linhas:
        # Extrai todos os valores monetários da linha
        valores = re.findall(r'R?\$?\s*([\d]+(?:[,.][\d]{2})?)', linha)
        if not valores:
            continue
        valor = 0.0
        for v in valores:
            v_f = float(v.replace(',', '.'))
            if v_f > 0:
                valor = v_f
                break
        if valor == 0:
            continue

        linha_lower = linha.lower()

        # Extrai nome: primeira palavra(s) antes de verbos-chave
        nome_m = re.match(r'^([A-ZÀ-Ú][a-zà-ú]+(?:\s+[A-ZÀ-Ú][a-zà-ú]+)*)', linha)
        nome = nome_m.group(1).strip() if nome_m else 'Cliente'

        if re.search(r'pag(ou|amento|ou\s+os|ou\s+o\s+rest)', linha_lower):
            fiados.append({
                'nome': nome,
                'tipo': 'CREDITO',
                'valor': valor,
                'descricao': linha[:120],
            })
        elif re.search(r'fiado', linha_lower):
            fiados.append({
                'nome': nome,
                'tipo': 'DEBITO',
                'valor': valor,
                'descricao': linha[:120],
            })

    return fiados


def _extrair_extras_notas(extras_texto: str) -> list[dict]:
    """
    Extrai itens de 'Extras:' — linhas com 'Nome: valor'.
    Ignora itens com tags 'Extra' ou pessoais (Netflix, Internet, Mãe etc.)
    conforme RN05.
    """
    ignorar = {'netflix', 'internet', 'mãe', 'mae', 'spotify', 'amazon', 'uber',
               'ifood', 'combustivel', 'combustível', 'gasolina', 'escola', 'faculdade'}
    extras = []
    for linha in extras_texto.split('\n'):
        linha = linha.strip()
        if not linha:
            continue
        m = re.match(r'^(.+?):\s*([\d,.]+)', linha)
        if m:
            desc = m.group(1).strip()
            if desc.lower() in ignorar:
                continue
            try:
                valor = float(m.group(2).replace('.', '').replace(',', '.'))
                extras.append({'descricao': desc, 'valor': valor})
            except ValueError:
                pass
    return extras


def parsear_notas_iphone(texto: str) -> list[dict]:
    """
    Recebe texto livre copiado do Bloco de Notas e retorna
    lista de feiras prontas para importação.
    """
    # Divide em blocos por data (linha que começa com dia da semana ou padrão de data)
    separador = re.compile(
        r'(?:Segunda|Terça|Terca|Quarta|Quinta|Sexta|Sábado|Sabado|Domingo)'
        r'[\s,]+\d{1,2}|'
        r'\d{1,2}\s+de\s+\w+\s+\d{4}|'
        r'\d{2}[/-]\d{2}[/-]\d{4}',
        re.IGNORECASE
    )

    posicoes = [m.start() for m in separador.finditer(texto)]
    if not posicoes:
        return []

    blocos = []
    for i, pos in enumerate(posicoes):
        fim = posicoes[i+1] if i+1 < len(posicoes) else len(texto)
        blocos.append(texto[pos:fim])

    feiras = []
    session = Session()
    try:
        for bloco in blocos:
            data_dt = _extrair_bloco_data(bloco)
            if not data_dt:
                continue

            # Pesos — linhas numéricas após "Peso:"
            lotes = []
            m_peso_section = re.search(r'Peso[:\s]*(.*?)(?:Preço|Preco|Caixa|$)', bloco, re.IGNORECASE | re.DOTALL)
            m_preco_section = re.search(r'Preç?o[:\s]*(.*?)(?:Caixa|$)', bloco, re.IGNORECASE | re.DOTALL)

            pesos = []
            precos = []

            if m_peso_section:
                raw = m_peso_section.group(1)
                pesos = [float(p.replace(',', '.')) for p in re.findall(r'[\d]+(?:[.,][\d]+)?', raw)
                         if float(p.replace(',', '.')) > 5]

            if m_preco_section:
                raw = m_preco_section.group(1)
                precos = [float(p.replace(',', '.')) for p in re.findall(r'[\d]+(?:[.,][\d]+)?', raw)
                          if float(p.replace(',', '.')) > 50]

            for i, peso in enumerate(pesos):
                preco = precos[i] if i < len(precos) else (precos[-1] if precos else 0.0)
                lotes.append({'peso': peso, 'preco': preco})

            # Caixa IN — pode ter soma "267,00 + 341,00"
            caixa_in = 0.0
            m_cin = re.search(r'[Cc]aixa\s+in[:\s]+([\d,.\s+]+)', bloco, re.IGNORECASE)
            if m_cin:
                caixa_in = _soma_expressao(m_cin.group(1))

            # Caixa OUT — pode ter soma "128,00 + 125,00"
            caixa_out = 0.0
            m_cout = re.search(r'[Cc]aixa\s+out[:\s]+([\d,.\s+]+)', bloco, re.IGNORECASE)
            if m_cout:
                caixa_out = _soma_expressao(m_cout.group(1))

            # Cartão
            total_cartao = 0.0
            m_cart = re.search(r'[Cc]art[ãa]o[:\s]+([\d,.]+)', bloco, re.IGNORECASE)
            if m_cart:
                total_cartao = float(m_cart.group(1).replace('.', '').replace(',', '.'))

            # Pix
            total_pix = 0.0
            m_pix = re.search(r'[Pp]ix[:\s]+([\d,.]+)', bloco, re.IGNORECASE)
            if m_pix:
                total_pix = float(m_pix.group(1).replace('.', '').replace(',', '.'))

            # Extras
            extras = []
            m_extra = re.search(r'[Ee]xtras?[:\s]*(.*?)(?:[Oo]bservações?|$)', bloco, re.DOTALL)
            if m_extra:
                extras = _extrair_extras_notas(m_extra.group(1))

            # Observações / fiados
            fiados = []
            m_obs = re.search(r'[Oo]bservações?[:\s]*(.*?)$', bloco, re.DOTALL)
            obs_raw = m_obs.group(1).strip() if m_obs else ''
            if obs_raw:
                fiados = _extrair_fiados_notas(obs_raw)

            feiras.append({
                'data': data_dt.strftime('%d/%m/%Y'),
                'caixa_in': caixa_in,
                'caixa_out': caixa_out,
                'total_pix': total_pix,
                'total_cartao': total_cartao,
                'lotes': lotes,
                'extras': extras,
                'fiados_detectados': fiados,
                'obs_raw': obs_raw,
                'ja_existe': _feira_ja_existe(session, data_dt),
            })
    finally:
        session.close()

    return feiras


# ─────────────────────────────────────────────
# IMPORTAÇÃO EFETIVA
# ─────────────────────────────────────────────

def importar_feiras(feiras: list[dict], importar_fiados: bool = True) -> dict:
    """
    Recebe lista de feiras parseadas e grava no banco.
    Retorna {'importadas': int, 'puladas': int, 'erros': int}.
    """
    importadas = puladas = erros = 0
    session = Session()
    try:
        for f in feiras:
            if f.get('ja_existe'):
                puladas += 1
                continue
            if not f.get('lotes'):
                puladas += 1
                continue
            try:
                data_dt = datetime.strptime(f['data'], '%d/%m/%Y').date()
                nova = Feira(
                    data=data_dt,
                    caixa_in=f['caixa_in'],
                    caixa_out=f['caixa_out'],
                    total_pix=f['total_pix'],
                    total_cartao=f['total_cartao'],
                    imposto=f.get('imposto', 0),
                    ativo=1,
                )
                session.add(nova)
                session.flush()

                for lote in f['lotes']:
                    session.add(Compra(
                        id_feira=nova.id,
                        peso_bruto=lote['peso'],
                        preco_arroba=lote['preco'],
                        sexo=lote.get('sexo', 'M'),
                    ))

                for e in f.get('extras', []):
                    session.add(ExtraFeira(
                        id_feira=nova.id,
                        descricao=e['descricao'],
                        valor=e['valor'],
                    ))

                if importar_fiados:
                    for fd in f.get('fiados_detectados', []):
                        nome = fd['nome'].strip().title()
                        if not nome:
                            continue
                        cliente = session.query(Cliente).filter(Cliente.nome.ilike(nome)).first()
                        if not cliente:
                            cliente = Cliente(nome=nome, saldo_devedor=0)
                            session.add(cliente)
                            session.flush()

                        delta = float(fd['valor']) if fd['tipo'] == 'DEBITO' else -float(fd['valor'])
                        novo_saldo = max(0.0, float(cliente.saldo_devedor) + delta)
                        cliente.saldo_devedor = Decimal(str(round(novo_saldo, 2)))

                        session.add(HistoricoFiado(
                            id_cliente=cliente.id,
                            id_feira=nova.id,
                            tipo=fd['tipo'],
                            valor=fd['valor'],
                            descricao=fd.get('descricao', '')[:200],
                        ))

                session.commit()
                importadas += 1
            except Exception as e:
                session.rollback()
                print(f"Erro ao importar feira {f.get('data')}: {e}")
                erros += 1
    finally:
        session.close()

    return {'importadas': importadas, 'puladas': puladas, 'erros': erros}