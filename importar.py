"""
Módulo de importação de dados para o SGB.

Suporta dois formatos:
  1. CSV exportado do Notion (colunas: Observações, Tags, Números, Datas)
  2. Texto livre do Bloco de Notas do iPhone

Correções v2:
  - Fix valores gigantes no CSV (formato US: vírgula=milhar, ponto=decimal)
  - Extras resetam após adicionar
  - Texto de notas reseta após importar
  - Fiados compostos: detecta crédito + débito na mesma linha
  - "4,5kg fiado" → converte pelo preço_kg_dia informado, ou marca como pendente
  - Casos mistos (pagou + levou fiado) → ambos extraídos e marcados para revisão
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

def _parse_valor_notion(s: str) -> float:
    """
    Converte valores do CSV do Notion para float.
    O Notion exporta no formato US puro: vírgula = milhar, ponto = decimal.
    Ex: '1,234.56' → 1234.56 | '302.00' → 302.0 | '-50.00' → -50.0
    """
    s = str(s).strip().replace('R$', '').replace(' ', '')
    negativo = s.startswith('-')
    s = s.lstrip('-+')
    # Remove vírgulas de milhar (ex: 1,234.56 → 1234.56)
    s = s.replace(',', '')
    try:
        v = float(s)
        return -v if negativo else v
    except ValueError:
        return 0.0


def _parse_valor_br(s: str) -> float:
    """
    Converte valores em formato BR para float.
    Ex: '267,00' → 267.0 | '1.234,56' → 1234.56
    """
    s = str(s).strip().replace('R$', '').replace(' ', '')
    negativo = s.startswith('-')
    s = s.lstrip('-')
    # Remove pontos de milhar e substitui vírgula decimal
    s = s.replace('.', '').replace(',', '.')
    try:
        v = float(s)
        return -v if negativo else v
    except ValueError:
        return 0.0


def _parse_peso_preco(obs: str):
    """
    Extrai lista de (peso, preco) de strings como:
      'Peso: 128.8(200@) + 122.5(180@)'   → dois lotes com preço inline
      'Peso: 148.0 (220@) + 112.2 (200@)' → dois lotes com espaço antes do (
      'Peso: 90+112=202 (180@/200@)'       → dois lotes com preços separados por /
      'Peso: 143.2 + 136.2 (250@)'         → dois pesos com preço compartilhado
      'Peso: 152.8 (200@)'                 → único lote
      'Peso: 149.7 (@OBS)'                 → preco=0, corrigir manualmente depois
    Retorna lista de dicts {'peso': float, 'preco': float}.
    """
    lotes = []
    obs = obs.strip()

    # Padrão 1: N+N=TOTAL (P1@/P2@) — ex: 90+112=202 (180@/200@)
    m_soma = re.match(r'Peso[:\s]*([\d.]+)\+([\d.]+)(?:=[\d.]+)?\s*\(([\d]+)@/([\d]+)@\)', obs)
    if m_soma:
        lotes.append({'peso': float(m_soma.group(1)), 'preco': float(m_soma.group(3))})
        lotes.append({'peso': float(m_soma.group(2)), 'preco': float(m_soma.group(4))})
        return lotes

    # Padrão 2: N+N (P@) — dois pesos, preço compartilhado — ex: 143.2 + 136.2 (250@)
    m_comp = re.match(r'Peso[:\s]*([\d.]+)\s*\+\s*([\d.]+)\s*\(([\d]+)@\)', obs)
    if m_comp:
        lotes.append({'peso': float(m_comp.group(1)), 'preco': float(m_comp.group(3))})
        lotes.append({'peso': float(m_comp.group(2)), 'preco': float(m_comp.group(3))})
        return lotes

    # Padrão 3: N.N(P@) ou N.N (P@) repetido — cada lote com seu preço inline
    padrao_inline = re.findall(r'([\d.]+)\s*\(([\d]+)@\)', obs)
    if padrao_inline:
        for peso_s, preco_s in padrao_inline:
            lotes.append({'peso': float(peso_s), 'preco': float(preco_s)})
        return lotes

    # Padrão 4: Peso: NNN (@OBS) — marcador sem preço, importa com preco=0
    m_obs = re.match(r'Peso[:\s]*([\d.]+)\s*\([^)]*@[A-Za-z][^)]*\)', obs)
    if m_obs:
        lotes.append({'peso': float(m_obs.group(1)), 'preco': 0.0})
        return lotes

    # Fallback: extrai só o peso, preco=0
    m2 = re.search(r'Peso[:\s]*([\d.]+)', obs)
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
    """
    texto = conteudo_bytes.decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(texto))

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
                # FIX: usa _parse_valor_notion para formato US do Notion
                valor = abs(_parse_valor_notion(num_s)) if num_s else 0.0

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
                continue

            feiras.append({
                'data': data_str,
                'caixa_in': round(caixa_in, 2),
                'caixa_out': round(caixa_out, 2),
                'total_pix': round(total_pix, 2),
                'total_cartao': round(total_cartao, 2),
                'lotes': lotes,
                'obs': ' | '.join(obs_list),
                'fiados_detectados': [],
                'ja_existe': _feira_ja_existe(session, data_dt),
            })
    finally:
        session.close()

    return feiras


# ─────────────────────────────────────────────
# PARSER 2 — TEXTO DO BLOCO DE NOTAS
# ─────────────────────────────────────────────

def _extrair_bloco_data(bloco: str):
    meses = {
        'janeiro': 1, 'fevereiro': 2, 'março': 3, 'marco': 3,
        'abril': 4, 'maio': 5, 'junho': 6, 'julho': 7,
        'agosto': 8, 'setembro': 9, 'outubro': 10,
        'novembro': 11, 'dezembro': 12,
    }
    m = re.search(r'(\d{1,2})\s+de\s+(\w+)\s+(\d{4})', bloco, re.IGNORECASE)
    if m:
        dia, mes_str, ano = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        mes = meses.get(mes_str)
        if mes:
            try:
                return datetime(ano, mes, dia)
            except ValueError:
                pass
    m2 = re.search(r'(\d{2})[/-](\d{2})[/-](\d{4})', bloco)
    if m2:
        try:
            return datetime(int(m2.group(3)), int(m2.group(2)), int(m2.group(1)))
        except ValueError:
            pass
    return None


def _soma_expressao(s: str) -> float:
    """Resolve '267,00 + 341,00' ou '128,00 + 125,00'."""
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


def _extrair_nome_inicio(linha: str) -> str:
    """Extrai o nome no início da linha (uma ou mais palavras capitalizadas)."""
    m = re.match(r'^([A-ZÀ-Ú][a-zA-Zà-úÀ-Ú]+(?:\s+[A-ZÀ-Ú][a-zA-Zà-úÀ-Ú]+)*)', linha.strip())
    return m.group(1).strip() if m else 'Cliente'


def _extrair_valor_reais(texto: str) -> float | None:
    """Extrai o primeiro valor monetário (R$) da string, ignorando quantidades em kg."""
    # Remove partes com kg para não confundir
    texto_sem_kg = re.sub(r'[\d.,]+\s*kg\b', '', texto, flags=re.IGNORECASE)
    m = re.search(r'R?\$?\s*([\d]+(?:[.,][\d]{1,2})?)', texto_sem_kg)
    if m:
        v = m.group(1).replace(',', '.')
        try:
            return float(v)
        except ValueError:
            pass
    return None


def _extrair_valor_kg(texto: str) -> float | None:
    """Extrai quantidade em kg da string. Ex: '4,5kg' → 4.5"""
    m = re.search(r'([\d]+(?:[.,][\d]+)?)\s*kg\b', texto, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1).replace(',', '.'))
        except ValueError:
            pass
    return None


def _extrair_fiados_notas(obs_texto: str, preco_kg_dia: float = 0.0) -> list[dict]:
    """
    Extrai movimentações de fiado das observações.

    Melhorias v2:
    - Detecta crédito + débito na mesma linha (casos mistos)
    - Converte kg → R$ usando preco_kg_dia se informado
    - Marca pendentes (kg sem preço) para revisão manual
    - Flag 'revisar' indica que o lançamento precisa de conferência

    Retorna lista de dicts:
    {
        'nome': str,
        'tipo': 'DEBITO' | 'CREDITO',
        'valor': float,
        'descricao': str,
        'revisar': bool,       # True = precisa revisão manual
        'motivo_revisao': str  # Explicação do que ficou pendente
    }
    """
    fiados = []
    linhas = [l.strip() for l in obs_texto.split('\n') if l.strip()]

    for linha in linhas:
        linha_lower = linha.lower()
        nome = _extrair_nome_inicio(linha)

        tem_pagou = bool(re.search(r'pag(ou|amento|ou\s+os|ou\s+o\s+rest)', linha_lower))
        tem_fiado = bool(re.search(r'fiado', linha_lower))
        tem_kg    = bool(re.search(r'[\d.,]+\s*kg\b', linha_lower))

        # ── CRÉDITO: detecta pagamento ──────────────────────────
        if tem_pagou:
            valor_pago = _extrair_valor_reais(linha)
            revisar = valor_pago is None
            fiados.append({
                'nome': nome,
                'tipo': 'CREDITO',
                'valor': valor_pago or 0.0,
                'descricao': linha[:120],
                'revisar': revisar or (tem_fiado),  # misto → sempre revisar
                'motivo_revisao': (
                    'Valor do pagamento não identificado — preencha manualmente.' if valor_pago is None
                    else ('Linha mista: pagamento + novo fiado detectados — confira os valores.' if tem_fiado else '')
                ),
            })

        # ── DÉBITO: detecta fiado ────────────────────────────────
        if tem_fiado:
            valor_fiado = _extrair_valor_reais(linha)
            kg_fiado = _extrair_valor_kg(linha) if tem_kg else None
            revisar = False
            motivo = ''

            if kg_fiado is not None:
                # Tem quantidade em kg
                if preco_kg_dia and preco_kg_dia > 0:
                    valor_fiado = round(kg_fiado * preco_kg_dia, 2)
                    motivo = f'{kg_fiado}kg × R${preco_kg_dia:.2f}/kg = R${valor_fiado:.2f}'
                else:
                    # Sem preço de referência → marca como pendente
                    valor_fiado = 0.0
                    revisar = True
                    motivo = f'{kg_fiado}kg sem preço/kg configurado — preencha o valor manualmente.'

            if valor_fiado is None or valor_fiado == 0.0:
                revisar = True
                motivo = motivo or 'Valor não identificado — preencha manualmente.'

            fiados.append({
                'nome': nome,
                'tipo': 'DEBITO',
                'valor': valor_fiado or 0.0,
                'descricao': linha[:120],
                'revisar': revisar or (tem_pagou),  # misto → sempre revisar
                'motivo_revisao': motivo or ('Linha mista: pagamento + novo fiado — confira os valores.' if tem_pagou else ''),
            })

        # ── Linha sem marcador claro — ignora ───────────────────

    return fiados


def _extrair_extras_notas(extras_texto: str) -> list[dict]:
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


def parsear_notas_iphone(texto: str, preco_kg_dia: float = 0.0) -> list[dict]:
    """
    Recebe texto livre do Bloco de Notas e retorna lista de feiras.
    preco_kg_dia: preço por kg informado pelo usuário para converter fiados em kg → R$.
    """
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

            # Pesos e Preços
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

            # Caixa IN/OUT
            caixa_in = 0.0
            m_cin = re.search(r'[Cc]aixa\s+in[:\s]+([\d,.\s+]+)', bloco, re.IGNORECASE)
            if m_cin:
                caixa_in = _soma_expressao(m_cin.group(1))

            caixa_out = 0.0
            m_cout = re.search(r'[Cc]aixa\s+out[:\s]+([\d,.\s+]+)', bloco, re.IGNORECASE)
            if m_cout:
                caixa_out = _soma_expressao(m_cout.group(1))

            # Cartão e Pix
            total_cartao = 0.0
            m_cart = re.search(r'[Cc]art[ãa]o[:\s]+([\d,.]+)', bloco, re.IGNORECASE)
            if m_cart:
                total_cartao = _parse_valor_br(m_cart.group(1))

            total_pix = 0.0
            m_pix = re.search(r'[Pp]ix[:\s]+([\d,.]+)', bloco, re.IGNORECASE)
            if m_pix:
                total_pix = _parse_valor_br(m_pix.group(1))

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
                fiados = _extrair_fiados_notas(obs_raw, preco_kg_dia=preco_kg_dia)

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
# PARSER 3 — ARQUIVO DE NOTAS (FORMATO ANTIGO + NOVO)
# ─────────────────────────────────────────────

def _detectar_formato_novo(bloco: str) -> bool:
    """Retorna True se o bloco usa o formato novo (Caixa in: / Caixa out:)."""
    return bool(re.search(r'[Cc]aixa\s+in[:\s]', bloco))


def _extrair_caixa_novo(bloco: str):
    m_in = re.search(r'[Cc]aixa\s+in[:\s]+([\.\d,\.\s+=]+)', bloco)
    m_out = re.search(r'[Cc]aixa\s+out[:\s]+([\.\d,\.\s+=]+)', bloco)
    return (_soma_expressao(m_in.group(1)) if m_in else 0.0,
            _soma_expressao(m_out.group(1)) if m_out else 0.0)


def _extrair_caixa_antigo(bloco: str):
    m_linha = re.search(r'[Cc]aixa[:\s]*(.*?)(?:\n|$)', bloco)
    if not m_linha:
        return 0.0, 0.0
    linha = m_linha.group(1).strip().replace('✅','').replace('——','').replace('—','').strip()
    pos = m_linha.end()
    proxima = ''
    for l in bloco[pos:].split('\n'):
        l = l.strip()
        if l:
            proxima = l.replace('✅', '').strip()
            break
    # X + Y (out na próxima linha)
    m = re.match(r'([\d.,]+)\s*\+\s*\(?([\d.,]+)\)?$', linha)
    if m:
        mp = re.match(r'\(?([\d.,]+)\)?', proxima)
        return (_parse_valor_br(m.group(1)) + _parse_valor_br(m.group(2)),
                _parse_valor_br(mp.group(1)) if mp else 0.0)
    # X + Y — (Z) ou X + Y = Z
    m = re.match(r'([\d.,]+)\s*\+\s*\(?([\d.,]+)\)?.*[\(=]([\d.,]+)\)?$', linha)
    if m and m.group(3) != m.group(1):
        return (_parse_valor_br(m.group(1)) + _parse_valor_br(m.group(2)),
                _parse_valor_br(m.group(3)))
    # -X / -Y(pix) / (Z)
    m = re.match(r'-?([\d.,]+)\s*/\s*-?([\d.,]+)\(?pix\)?\s*/\s*\(?([\d.,]+)', linha, re.IGNORECASE)
    if m:
        return (_parse_valor_br(m.group(1)) + _parse_valor_br(m.group(2)),
                _parse_valor_br(m.group(3)))
    # -X (+Y)
    m = re.match(r'-?([\d.,]+)\s*\(\+?([\d.,]+)\)', linha)
    if m:
        return _parse_valor_br(m.group(2)), _parse_valor_br(m.group(1))
    # X (-Y)
    m = re.match(r'([\d.,]+)\s*\(-?([\d.,]+)\)', linha)
    if m:
        return _parse_valor_br(m.group(1)), _parse_valor_br(m.group(2))
    # total X
    m_tot = re.search(r'total\s+([\d.,]+)', linha, re.IGNORECASE)
    if m_tot:
        nums = re.findall(r'[\d.,]+', m_linha.group(1))
        vals = [_parse_valor_br(n) for n in nums if _parse_valor_br(n) > 0]
        if len(vals) >= 2:
            return sum(vals[:-1]), vals[-1]
        if vals:
            return 0.0, vals[0]
    # X (Y)
    m = re.match(r'([\d.,]+)\s+\(?\s*([\d.,]+)\s*\)?', linha)
    if m:
        return _parse_valor_br(m.group(1)), _parse_valor_br(m.group(2))
    # só X
    m = re.match(r'([\d.,]+)', linha)
    if m:
        return 0.0, _parse_valor_br(m.group(1))
    return 0.0, 0.0


def _extrair_cartao_arquivo(bloco: str, formato_novo: bool) -> float:
    if formato_novo:
        m = re.search(r'[Cc]art[ãao]o[:\s]*([\d.,]+)', bloco)
        return _parse_valor_br(m.group(1)) if m else 0.0
    # Formato antigo: tenta "Cartões: X" ou seção Eu
    m_cart = re.search(r'[Cc]art[õo]es?[:\s]+([\d.,]+)', bloco)
    if m_cart:
        return _parse_valor_br(m_cart.group(1))
    m_eu = re.search(
        r'\bEu[:\s]*\n(.*?)(?:\n(?:Mãe|Mae|Pai|N°|Carnes|Fiado|Dinheiro|Observ|Quebras|Extras|Daniel|Ionara)\b|$)',
        bloco, re.DOTALL | re.IGNORECASE)
    if not m_eu:
        return 0.0
    totais = []
    for linha in m_eu.group(1).split('\n'):
        linha = linha.strip().replace('(', '').replace(')', '')
        if not linha or ' - ' in linha or '+' in linha:
            continue
        m = re.match(r'^([\d.]+,\d{2})$', linha)
        if m:
            v = _parse_valor_br(m.group(1))
            if v > 50:
                totais.append(v)
    return totais[-1] if totais else 0.0


def _extrair_lotes_arquivo(bloco: str) -> list:
    """Extrai lotes do formato novo; retorna placeholder para formato antigo."""
    m_peso = re.search(r'Peso[:\s]*(.*?)(?:Preço|Preco|Caixa|$)', bloco, re.IGNORECASE | re.DOTALL)
    m_preco = re.search(r'Preç?o[:\s]*(.*?)(?:Caixa|$)', bloco, re.IGNORECASE | re.DOTALL)
    pesos, precos = [], []
    if m_peso:
        raw = m_peso.group(1)
        pesos = [float(p.replace(',', '.')) for p in re.findall(r'[\d]+(?:[.,][\d]+)?', raw)
                 if float(p.replace(',', '.')) > 5]
    if m_preco:
        raw = m_preco.group(1)
        precos = [float(p.replace(',', '.')) for p in re.findall(r'[\d]+(?:[.,][\d]+)?', raw)
                  if float(p.replace(',', '.')) > 50]
    lotes = []
    for i, peso in enumerate(pesos):
        preco = precos[i] if i < len(precos) else (precos[-1] if precos else 0.0)
        lotes.append({'peso': peso, 'preco': preco, 'sexo': 'M'})
    return lotes if lotes else [{'peso': 1, 'preco': 0, 'sexo': 'M'}]


def _detectar_formato_2024(bloco: str) -> bool:
    """Formato 2024: Peso com preço inline e Caixa: X - (Y)"""
    return (bool(re.search(r'Peso[:\s]*[\d.]+\s*\(', bloco, re.IGNORECASE)) and
            not bool(re.search(r'[Cc]aixa\s+in[:\s]', bloco)))


def _extrair_peso_preco_2024(bloco: str):
    padroes = [
        r'Peso[:\s]*([\d.]+)\s*\(comprado\s+a\s+([\d.,]+)',
        r'Peso[:\s]*([\d.]+)\s*\(\s*a\s+([\d.,]+)',
        r'Peso[:\s]*([\d.]+)\s*\(([\d.,]+)\s*a\s+@',
        r'Peso[:\s]*([\d.]+)\s*\(([\d.,]+)@',
        r'Peso[:\s]*([\d.]+)\s*\(([\d.,]+)\s*@',
    ]
    for padrao in padroes:
        m = re.search(padrao, bloco, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1)), _parse_valor_br(m.group(2))
            except:
                pass
    m = re.search(r'Peso[:\s]*([\d.]+)', bloco, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1)), 0.0
        except:
            pass
    return 0.0, 0.0


def _extrair_caixa_2024(bloco: str):
    m = re.search(r'[Cc]aixa[:\s]*([\d.,]+)\s*[-–]\s*\(?([\d.,]+)', bloco)
    if m:
        return _parse_valor_br(m.group(1)), _parse_valor_br(m.group(2))
    m = re.search(r'[Cc]aixa[:\s]*([\d.,]+)', bloco)
    if m:
        return 0.0, _parse_valor_br(m.group(1))
    return 0.0, 0.0


def _extrair_pix_2024(bloco: str) -> float:
    m_total = re.search(r'[Pp]ix[:\s]*\(([\d.,]+)\)', bloco)
    if m_total:
        return _parse_valor_br(m_total.group(1))
    m_section = re.search(r'[Pp]ix[:\s]*\n((?:\s*[\d.,]+\n)+)', bloco)
    if m_section:
        vals = re.findall(r'[\d.,]+', m_section.group(1))
        return round(sum(_parse_valor_br(v) for v in vals if _parse_valor_br(v) > 5), 2)
    m = re.search(r'[Pp]ix[:\s]*([\d.,]+)', bloco)
    return _parse_valor_br(m.group(1)) if m else 0.0


def _extrair_cartao_2024(bloco: str) -> float:
    m = re.search(r'[Cc]art[ãao]o[:\s]*\n?\s*\(?([\d.,]+)\)?', bloco)
    return _parse_valor_br(m.group(1)) if m else 0.0


def parsear_arquivo_notas(texto: str, preco_kg_dia: float = 0.0) -> list[dict]:
    """
    Parser unificado para arquivos de notas (formato antigo e novo).
    Detecta automaticamente o formato de cada bloco.
    Feiras sem caixa identificável são puladas com aviso.
    Lotes sem peso (formato antigo) entram com peso=1, preco=0.
    """
    separador = re.compile(
        r'(?:Segunda|Terça|Terca|Quarta|Quinta|Sexta|Sábado|Sabado|Domingo)' +
        r'[\s,dia\(]*\d{1,2}|' +
        r'Dia\s+\d{1,2}\s+de\s+\w+|' +
        r'\d{1,2}\s+de\s+\w+(?:\s+de)?\s+\d{4}',
        re.IGNORECASE)
    posicoes = [m.start() for m in separador.finditer(texto)]
    if not posicoes:
        return []
    blocos = [texto[posicoes[i]:posicoes[i+1] if i+1 < len(posicoes) else len(texto)]
              for i in range(len(posicoes))]

    session = Session()
    feiras = []
    try:
        for bloco in blocos:
            data_dt = _extrair_bloco_data(bloco)
            if not data_dt:
                continue
            ja_existe = _feira_ja_existe(session, data_dt)
            fmt_novo = _detectar_formato_novo(bloco)

            fmt_2024 = _detectar_formato_2024(bloco)

            if fmt_novo:
                cin, cout = _extrair_caixa_novo(bloco)
                pix_raw = re.search(r'[Pp]ix[:\s]*([\.\d.,+\s=]+)', bloco)
                pix = _soma_expressao(pix_raw.group(1)) if pix_raw else 0.0
                cartao = _extrair_cartao_arquivo(bloco, True)
            elif fmt_2024:
                cin, cout = _extrair_caixa_2024(bloco)
                pix = _extrair_pix_2024(bloco)
                cartao = _extrair_cartao_2024(bloco)
            else:
                cin, cout = _extrair_caixa_antigo(bloco)
                pix_raw = re.search(r'[Pp]ix[:\s]*([\.\d.,+\s=]+)', bloco)
                pix = _soma_expressao(pix_raw.group(1)) if pix_raw else 0.0
                cartao = _extrair_cartao_arquivo(bloco, False)

            lotes = _extrair_lotes_arquivo(bloco) if (fmt_novo or fmt_2024) else [{'peso': 1, 'preco': 0, 'sexo': 'M'}]
            # Para 2024, usa peso/preço da linha de Peso
            if fmt_2024:
                peso, preco = _extrair_peso_preco_2024(bloco)
                if peso > 0:
                    lotes = [{'peso': peso, 'preco': preco, 'sexo': 'M'}]

            # Fiados e extras só no formato novo
            obs_inicio = bloco.find('Observ')
            fiados = _extrair_fiados_notas(
                bloco[obs_inicio:] if obs_inicio >= 0 else '',
                preco_kg_dia) if fmt_novo else []
            m_extra = re.search(
                r'[Ee]xtras?[:\s]*(.*?)(?:[Oo]bservações?|[Cc]arnes|[Ee]mpréstimos|[Qq]uebras|$)',
                bloco, re.DOTALL)
            extras = _extrair_extras_notas(m_extra.group(1) if m_extra else '') if fmt_novo else []

            if cout == 0 and cartao == 0 and cin == 0:
                feiras.append({
                    'data': data_dt.strftime('%d/%m/%Y'),
                    'caixa_in': 0.0, 'caixa_out': 0.0,
                    'total_pix': 0.0, 'total_cartao': 0.0,
                    'lotes': lotes, 'extras': [], 'fiados_detectados': [],
                    'obs_raw': '', 'ja_existe': ja_existe,
                    'formato_antigo': not fmt_novo,
                    'pulada': True, 'motivo_pulada': 'Caixa não identificado — preencha manualmente',
                })
                continue

            feiras.append({
                'data': data_dt.strftime('%d/%m/%Y'),
                'caixa_in': round(cin, 2),
                'caixa_out': round(cout, 2),
                'total_pix': round(pix, 2),
                'total_cartao': round(cartao, 2),
                'lotes': lotes,
                'extras': extras,
                'fiados_detectados': fiados,
                'obs_raw': '',
                'ja_existe': ja_existe,
                'formato_antigo': not fmt_novo,
                'pulada': False,
                'motivo_pulada': '',
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
    Fiados com valor == 0 ou revisar == True são pulados automaticamente
    (o usuário deve lançá-los manualmente).
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
                        # Pula fiados que precisam de revisão ou com valor zero
                        if fd.get('revisar') or fd.get('valor', 0) == 0:
                            continue
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
