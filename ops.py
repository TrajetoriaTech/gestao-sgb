from database import Session, Feira, Compra, Cliente, HistoricoFiado, Configuracao, ExtraFeira
from datetime import datetime, timedelta
from decimal import Decimal

# ─────────────────────────────────────────────
# UTILITÁRIOS INTERNOS
# ─────────────────────────────────────────────

def _get_fator_quebra(session):
    cfg = session.query(Configuracao).filter_by(chave='fator_quebra').first()
    return float(cfg.valor) if cfg else 0.10


def _get_threshold_fiado(session):
    cfg = session.query(Configuracao).filter_by(chave='threshold_fiado').first()
    return float(cfg.valor) if cfg else 0.15


# ─────────────────────────────────────────────
# FEIRAS
# ─────────────────────────────────────────────

def registrar_feira_completa(data_str, c_in, c_out, pix, cartao, imposto, lista_gados, lista_fiados=[]):
    """Registra uma feira. Imposto é informativo — já descontado do caixa."""
    session = Session()
    try:
        data_formatada = datetime.strptime(data_str, "%d/%m/%Y").date()

        nova_feira = Feira(
            data=data_formatada,
            caixa_in=c_in,
            caixa_out=c_out,
            total_pix=pix,
            total_cartao=cartao,
            imposto=imposto,
            ativo=1
        )
        session.add(nova_feira)
        session.flush()

        for gado in lista_gados:
            session.add(Compra(
                id_feira=nova_feira.id,
                peso_bruto=gado['peso'],
                preco_arroba=gado['preco'],
                sexo=gado.get('sexo', 'M')
            ))

        for fiado in lista_fiados:
            nome_normalizado = fiado['nome'].strip().title()
            cliente = session.query(Cliente).filter(Cliente.nome.ilike(nome_normalizado)).first()
            if not cliente:
                cliente = Cliente(nome=nome_normalizado, saldo_devedor=0)
                session.add(cliente)
                session.flush()

            novo_saldo = float(cliente.saldo_devedor) + float(fiado['valor'])
            cliente.saldo_devedor = Decimal(str(novo_saldo))
            session.add(HistoricoFiado(
                id_cliente=cliente.id,
                id_feira=nova_feira.id,
                tipo='DEBITO',
                valor=fiado['valor'],
                descricao=f"Fiado registrado na feira de {data_str}"
            ))

        session.commit()
        return True
    except Exception as e:
        session.rollback()
        print(f"Erro ao registrar feira: {e}")
        return False
    finally:
        session.close()


def atualizar_feira_completa(id_feira, c_in, c_out, pix, cartao, imposto, lista_gados_nova):
    """Atualiza caixa, imposto e lotes de uma feira existente."""
    session = Session()
    try:
        feira = session.query(Feira).filter_by(id=id_feira).first()
        if not feira:
            return False
        feira.caixa_in = c_in
        feira.caixa_out = c_out
        feira.total_pix = pix
        feira.total_cartao = cartao
        feira.imposto = imposto
        session.query(Compra).filter_by(id_feira=id_feira).delete()
        for gado in lista_gados_nova:
            session.add(Compra(
                id_feira=id_feira,
                peso_bruto=gado['peso'],
                preco_arroba=gado['preco'],
                sexo=gado.get('sexo', 'M')
            ))
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        print(f"Erro ao atualizar feira {id_feira}: {e}")
        return False
    finally:
        session.close()


def excluir_definitivo(id_feira):
    session = Session()
    try:
        session.query(HistoricoFiado).filter_by(id_feira=id_feira).delete()
        session.query(ExtraFeira).filter_by(id_feira=id_feira).delete()
        session.query(Compra).filter_by(id_feira=id_feira).delete()
        session.query(Feira).filter_by(id=id_feira).delete()
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        print(f"Erro ao excluir feira {id_feira}: {e}")
        return False
    finally:
        session.close()


def esvaziar_lixeira():
    session = Session()
    try:
        ids = [f.id for f in session.query(Feira).filter_by(ativo=0).all()]
        if ids:
            session.query(HistoricoFiado).filter(HistoricoFiado.id_feira.in_(ids)).delete(synchronize_session=False)
            session.query(ExtraFeira).filter(ExtraFeira.id_feira.in_(ids)).delete(synchronize_session=False)
            session.query(Compra).filter(Compra.id_feira.in_(ids)).delete(synchronize_session=False)
            session.query(Feira).filter(Feira.id.in_(ids)).delete(synchronize_session=False)
            session.commit()
            return len(ids)
        return 0
    except Exception as e:
        session.rollback()
        print(f"Erro ao esvaziar lixeira: {e}")
        return -1
    finally:
        session.close()


# ─────────────────────────────────────────────
# EXTRAS (informativos)
# ─────────────────────────────────────────────

def registrar_extra(id_feira, descricao, valor):
    session = Session()
    try:
        session.add(ExtraFeira(id_feira=id_feira, descricao=descricao.strip(), valor=valor))
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        print(f"Erro ao registrar extra: {e}")
        return False
    finally:
        session.close()


def excluir_extra(id_extra):
    session = Session()
    try:
        session.query(ExtraFeira).filter_by(id=id_extra).delete()
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        return False
    finally:
        session.close()


def buscar_extras_feira(id_feira):
    session = Session()
    try:
        extras = session.query(ExtraFeira).filter_by(id_feira=id_feira).all()
        return [{'id': e.id, 'descricao': e.descricao, 'valor': float(e.valor)} for e in extras]
    finally:
        session.close()


# ─────────────────────────────────────────────
# FIADOS
# ─────────────────────────────────────────────

def registrar_fiado_avulso(nome_cliente, valor, descricao=""):
    session = Session()
    try:
        if valor <= 0:
            return False, "O valor do fiado deve ser maior que zero."
        nome_normalizado = nome_cliente.strip().title()
        if not nome_normalizado:
            return False, "O nome do cliente não pode estar vazio."

        cliente = session.query(Cliente).filter(Cliente.nome.ilike(nome_normalizado)).first()
        if not cliente:
            cliente = Cliente(nome=nome_normalizado, saldo_devedor=0)
            session.add(cliente)
            session.flush()

        novo_saldo = float(cliente.saldo_devedor) + float(valor)
        cliente.saldo_devedor = Decimal(str(novo_saldo))
        session.add(HistoricoFiado(
            id_cliente=cliente.id,
            id_feira=None,
            tipo='DEBITO',
            valor=valor,
            descricao=descricao.strip() if descricao else "Fiado avulso"
        ))
        session.commit()
        return True, "Fiado registrado com sucesso."
    except Exception as e:
        session.rollback()
        return False, f"Erro ao registrar fiado: {e}"
    finally:
        session.close()


def registrar_pagamento_fiado(id_cliente, valor):
    """Abate o saldo devedor corretamente usando reatribuição explícita."""
    session = Session()
    try:
        if valor <= 0:
            return False
        cliente = session.query(Cliente).filter_by(id=id_cliente).first()
        if not cliente:
            return False

        saldo_atual = float(cliente.saldo_devedor)
        valor_real = min(float(valor), saldo_atual)
        novo_saldo = round(saldo_atual - valor_real, 2)
        cliente.saldo_devedor = Decimal(str(novo_saldo))

        session.add(HistoricoFiado(
            id_cliente=id_cliente,
            id_feira=None,
            tipo='CREDITO',
            valor=valor_real,
            descricao="Pagamento recebido"
        ))
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        print(f"Erro ao registrar pagamento: {e}")
        return False
    finally:
        session.close()


def buscar_historico_cliente(id_cliente):
    """Retorna movimentações e totais de débito/crédito do cliente."""
    session = Session()
    try:
        historico = session.query(HistoricoFiado).filter_by(
            id_cliente=id_cliente
        ).order_by(HistoricoFiado.data.asc()).all()

        movs = [{
            'data': h.data.strftime('%d/%m/%Y %H:%M'),
            'tipo': h.tipo,
            'valor': float(h.valor),
            'descricao': h.descricao or '',
            'id_feira': h.id_feira
        } for h in historico]

        total_devido = round(sum(m['valor'] for m in movs if m['tipo'] == 'DEBITO'), 2)
        total_pago   = round(sum(m['valor'] for m in movs if m['tipo'] == 'CREDITO'), 2)

        return {
            'movimentacoes': movs,
            'total_devido': total_devido,
            'total_pago': total_pago,
        }
    finally:
        session.close()


def calcular_saude_fiados():
    """
    Retorna métricas de saúde do portfólio de fiados:
    - total_em_aberto: soma de todos os saldos devedores ativos
    - faturamento_total: soma do faturamento real de todas as feiras ativas
    - percentual_fiado: total_em_aberto / faturamento_total * 100
    - threshold: limite configurado (default 15%)
    - em_alerta: bool indicando se ultrapassou o threshold
    - total_devedores: quantidade de clientes com saldo > 0
    - aging: breakdown por faixa de dias do débito mais antigo de cada cliente
        - ate_30: clientes com débito mais antigo <= 30 dias
        - de_31_a_60: clientes com débito mais antigo entre 31 e 60 dias
        - acima_60: clientes com débito mais antigo > 60 dias
    - detalhe_aging: lista com {nome, saldo, dias_em_aberto, faixa} por cliente devedor
    """
    session = Session()
    try:
        threshold = _get_threshold_fiado(session)

        # Faturamento total das feiras ativas (base de comparação)
        feiras = session.query(Feira).filter_by(ativo=1).all()
        faturamento_total = 0.0
        for f in feiras:
            compras = session.query(Compra).filter_by(id_feira=f.id).all()
            custo_gado = sum((float(c.preco_arroba) / 15) * float(c.peso_bruto) for c in compras)
            fiados_dia = sum(
                float(h.valor) for h in
                session.query(HistoricoFiado).filter_by(id_feira=f.id, tipo='DEBITO').all()
            )
            v_caixa = float(f.caixa_out) + float(f.total_pix) + float(f.total_cartao)
            lucro_liq = v_caixa - float(f.caixa_in)
            fat_real = lucro_liq + custo_gado + fiados_dia
            faturamento_total += fat_real

        # Clientes com saldo devedor
        devedores = session.query(Cliente).filter(Cliente.saldo_devedor > 0).all()
        total_em_aberto = sum(float(d.saldo_devedor) for d in devedores)

        percentual_fiado = round((total_em_aberto / faturamento_total * 100), 2) if faturamento_total > 0 else 0.0
        em_alerta = percentual_fiado > (threshold * 100)

        # Aging: data do débito mais antigo ainda não quitado por cliente
        hoje = datetime.now()
        detalhe_aging = []
        aging = {'ate_30': 0, 'de_31_a_60': 0, 'acima_60': 0}

        for d in devedores:
            # Pega o débito mais antigo deste cliente
            debito_mais_antigo = (
                session.query(HistoricoFiado)
                .filter_by(id_cliente=d.id, tipo='DEBITO')
                .order_by(HistoricoFiado.data.asc())
                .first()
            )
            if debito_mais_antigo:
                dias = (hoje - debito_mais_antigo.data).days
            else:
                dias = 0

            if dias <= 30:
                faixa = '🟢 até 30 dias'
                aging['ate_30'] += 1
            elif dias <= 60:
                faixa = '🟡 31–60 dias'
                aging['de_31_a_60'] += 1
            else:
                faixa = '🔴 acima de 60 dias'
                aging['acima_60'] += 1

            detalhe_aging.append({
                'nome': d.nome,
                'saldo': float(d.saldo_devedor),
                'dias_em_aberto': dias,
                'faixa': faixa,
            })

        # Ordena por dias em aberto (mais crítico primeiro)
        detalhe_aging.sort(key=lambda x: x['dias_em_aberto'], reverse=True)

        return {
            'total_em_aberto': round(total_em_aberto, 2),
            'faturamento_total': round(faturamento_total, 2),
            'percentual_fiado': percentual_fiado,
            'threshold': threshold * 100,
            'em_alerta': em_alerta,
            'total_devedores': len(devedores),
            'aging': aging,
            'detalhe_aging': detalhe_aging,
        }
    finally:
        session.close()


def salvar_threshold_fiado(novo_valor: float) -> bool:
    """Salva o threshold de alerta de fiado (ex: 0.15 para 15%)."""
    session = Session()
    try:
        cfg = session.query(Configuracao).filter_by(chave='threshold_fiado').first()
        if cfg:
            cfg.valor = novo_valor
        else:
            session.add(Configuracao(chave='threshold_fiado', valor=novo_valor))
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        print(f"Erro ao salvar threshold: {e}")
        return False
    finally:
        session.close()


def salvar_fator_quebra(novo_valor: float) -> bool:
    """Salva o fator de quebra (ex: 0.10 para 10%). Afeta todos os cálculos de peso líquido."""
    session = Session()
    try:
        cfg = session.query(Configuracao).filter_by(chave='fator_quebra').first()
        if cfg:
            cfg.valor = novo_valor
        else:
            session.add(Configuracao(chave='fator_quebra', valor=novo_valor))
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        print(f"Erro ao salvar fator de quebra: {e}")
        return False
    finally:
        session.close()


# ─────────────────────────────────────────────
# PREVISÃO / SIMULAÇÃO
# ─────────────────────────────────────────────

def calcular_metricas_historicas():
    session = Session()
    try:
        fator_quebra = _get_fator_quebra(session)
        feiras = session.query(Feira).filter_by(ativo=1).all()
        if not feiras:
            return None

        registros = []
        for f in feiras:
            compras = session.query(Compra).filter_by(id_feira=f.id).all()
            if not compras:
                continue

            total_kg_bruto = sum(float(c.peso_bruto) for c in compras)
            total_kg_liquido = total_kg_bruto * (1 - fator_quebra)
            custo_gado = sum((float(c.preco_arroba) / 15) * float(c.peso_bruto) for c in compras)

            fiados_dia = sum(
                float(h.valor) for h in
                session.query(HistoricoFiado).filter_by(id_feira=f.id, tipo='DEBITO').all()
            )

            total_extras = sum(float(e.valor) for e in session.query(ExtraFeira).filter_by(id_feira=f.id).all())
            imposto = float(f.imposto) if f.imposto else 0

            v_caixa = float(f.caixa_out) + float(f.total_pix) + float(f.total_cartao)
            lucro_liq = v_caixa - float(f.caixa_in)
            fat_real = lucro_liq + custo_gado + fiados_dia

            if total_kg_bruto > 0:
                registros.append({
                    'kg_bruto': total_kg_bruto,
                    'kg_liquido': total_kg_liquido,
                    'custo_por_kg': custo_gado / total_kg_bruto,
                    'faturamento_por_kg': fat_real / total_kg_bruto if fat_real > 0 else 0,
                    'lucro_por_kg': lucro_liq / total_kg_liquido if total_kg_liquido > 0 else 0,
                    'margem': (lucro_liq / fat_real * 100) if fat_real > 0 else 0,
                    'proporcao_pix': float(f.total_pix) / v_caixa if v_caixa > 0 else 0,
                    'proporcao_cartao': float(f.total_cartao) / v_caixa if v_caixa > 0 else 0,
                    'proporcao_especie': float(f.caixa_out) / v_caixa if v_caixa > 0 else 0,
                    'preco_arroba_medio': sum(float(c.preco_arroba) for c in compras) / len(compras),
                    'extras': total_extras,
                    'imposto': imposto,
                })

        if not registros:
            return None

        n = len(registros)
        total_kg = sum(r['kg_bruto'] for r in registros)
        total_kg_liq = sum(r['kg_liquido'] for r in registros)

        # Média ponderada por kg bruto — feiras maiores influenciam proporcionalmente mais.
        # Ex: soma(faturamento_por_kg * kg) / soma(kg) em vez de soma(fat/kg) / n_feiras.
        def _pond_bruto(campo):
            return sum(r[campo] * r['kg_bruto'] for r in registros) / total_kg

        def _pond_liq(campo):
            return sum(r[campo] * r['kg_liquido'] for r in registros) / total_kg_liq

        # Proporções de pagamento: ponderadas pelo volume financeiro (kg bruto é proxy razoável)
        def _pond_prop(campo):
            return sum(r[campo] * r['kg_bruto'] for r in registros) / total_kg

        return {
            'n_feiras': n,
            'fator_quebra': fator_quebra,
            'custo_por_kg':        round(_pond_bruto('custo_por_kg'), 4),
            'faturamento_por_kg':  round(_pond_bruto('faturamento_por_kg'), 4),
            'lucro_por_kg':        round(_pond_liq('lucro_por_kg'), 4),
            'margem_media':        round(_pond_bruto('margem'), 2),
            'proporcao_pix':       round(_pond_prop('proporcao_pix'), 4),
            'proporcao_cartao':    round(_pond_prop('proporcao_cartao'), 4),
            'proporcao_especie':   round(_pond_prop('proporcao_especie'), 4),
            'preco_arroba_medio':  round(_pond_bruto('preco_arroba_medio'), 2),
            'kg_medio_por_feira':  round(total_kg / n, 1),
            'extras_medio':        round(sum(r['extras'] for r in registros) / n, 2),
            'imposto_medio':       round(sum(r['imposto'] for r in registros) / n, 2),
        }
    finally:
        session.close()


def simular_feira(peso_kg, preco_arroba, metricas):
    fator_quebra = metricas['fator_quebra']
    kg_liquido = peso_kg * (1 - fator_quebra)
    custo = round((preco_arroba / 15) * peso_kg, 2)
    faturamento_proj = round(metricas['faturamento_por_kg'] * peso_kg, 2)
    lucro_proj = round(metricas['lucro_por_kg'] * kg_liquido, 2)
    margem_proj = round((lucro_proj / faturamento_proj * 100) if faturamento_proj > 0 else 0, 1)
    receita_total = round(lucro_proj + custo, 2)
    pix_proj = round(receita_total * metricas['proporcao_pix'], 2)
    cartao_proj = round(receita_total * metricas['proporcao_cartao'], 2)
    especie_proj = round(receita_total * metricas['proporcao_especie'], 2)

    return {
        'peso_bruto': peso_kg,
        'peso_liquido': round(kg_liquido, 1),
        'arrobas': round(peso_kg / 15, 2),
        'custo': custo,
        'faturamento': faturamento_proj,
        'lucro': lucro_proj,
        'margem': margem_proj,
        'lucro_por_kg': round(lucro_proj / kg_liquido, 2) if kg_liquido > 0 else 0,
        'pix_proj': pix_proj,
        'cartao_proj': cartao_proj,
        'especie_proj': especie_proj,
    }


# ─────────────────────────────────────────────
# EXPORTAÇÃO
# ─────────────────────────────────────────────

def exportar_csv():
    import pandas as pd
    import io

    session = Session()
    try:
        fator_quebra = _get_fator_quebra(session)
        feiras = session.query(Feira).filter_by(ativo=1).all()

        dados = []
        for f in feiras:
            compras = session.query(Compra).filter_by(id_feira=f.id).all()
            total_kg_bruto = sum(float(c.peso_bruto) for c in compras)
            total_kg_liquido = round(total_kg_bruto * (1 - fator_quebra), 3)
            custo_gado = round(sum((float(c.preco_arroba) / 15) * float(c.peso_bruto) for c in compras), 2)
            fiados_dia = sum(
                float(h.valor) for h in
                session.query(HistoricoFiado).filter_by(id_feira=f.id, tipo='DEBITO').all()
            )
            total_extras = sum(float(e.valor) for e in session.query(ExtraFeira).filter_by(id_feira=f.id).all())
            imposto = float(f.imposto) if f.imposto else 0
            v_caixa = float(f.caixa_out) + float(f.total_pix) + float(f.total_cartao)
            lucro_liq = round(v_caixa - float(f.caixa_in), 2)
            fat_real = round(lucro_liq + custo_gado + fiados_dia, 2)

            dados.append({
                "ID": f.id,
                "Data": f.data.strftime('%d/%m/%Y'),
                "Caixa_In": float(f.caixa_in),
                "Caixa_Out": float(f.caixa_out),
                "Pix": float(f.total_pix),
                "Cartao": float(f.total_cartao),
                "Imposto (info)": imposto,
                "KG_Bruto": round(total_kg_bruto, 3),
                "KG_Liquido": total_kg_liquido,
                "Custo_Gado": custo_gado,
                "Fiados_Dia": fiados_dia,
                "Extras (info)": total_extras,
                "Faturamento": fat_real,
                "Lucro_Liquido": lucro_liq,
            })

        df_feiras = pd.DataFrame(dados)
        clientes = session.query(Cliente).all()
        df_clientes = pd.DataFrame([{
            "ID": c.id, "Nome": c.nome, "Saldo_Devedor": float(c.saldo_devedor)
        } for c in clientes])

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_feiras.to_excel(writer, sheet_name='Feiras', index=False)
            df_clientes.to_excel(writer, sheet_name='Clientes', index=False)
        output.seek(0)
        return output
    finally:
        session.close()