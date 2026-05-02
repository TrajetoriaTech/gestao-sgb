from sqlalchemy import create_engine, Column, Integer, String, Numeric, Date, ForeignKey, CheckConstraint, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime

# 1. Configuração do Banco
engine = create_engine('sqlite:///banca.db')
Session = sessionmaker(bind=engine)
Base = declarative_base()

# 2. Definição das Tabelas

class Configuracao(Base):
    __tablename__ = 'configuracoes'
    id = Column(Integer, primary_key=True)
    chave = Column(String, unique=True, nullable=False)
    valor = Column(Numeric(10, 2), nullable=False)

class Feira(Base):
    __tablename__ = 'feiras'
    id = Column(Integer, primary_key=True)
    data = Column(Date, nullable=False)
    caixa_in = Column(Numeric(10, 2), nullable=False)
    caixa_out = Column(Numeric(10, 2), nullable=False)
    total_pix = Column(Numeric(10, 2), default=0)
    total_cartao = Column(Numeric(10, 2), default=0)
    imposto = Column(Numeric(10, 2), default=0)    # informativo — já descontado do caixa
    ativo = Column(Integer, default=1)              # 1=Ativo, 0=Lixeira

class Compra(Base):
    __tablename__ = 'compras'
    id = Column(Integer, primary_key=True)
    id_feira = Column(Integer, ForeignKey('feiras.id'), nullable=False)
    peso_bruto = Column(Numeric(10, 3), nullable=False)
    preco_arroba = Column(Numeric(10, 2), nullable=False)
    sexo = Column(String, default='M')             # 'M'=Macho, 'F'=Fêmea

class Cliente(Base):
    __tablename__ = 'clientes'
    id = Column(Integer, primary_key=True)
    nome = Column(String, nullable=False, unique=True)
    telefone = Column(String)
    saldo_devedor = Column(Numeric(10, 2), default=0)

class HistoricoFiado(Base):
    __tablename__ = 'historico_fiado'
    id = Column(Integer, primary_key=True)
    id_cliente = Column(Integer, ForeignKey('clientes.id'), nullable=False)
    id_feira = Column(Integer, ForeignKey('feiras.id'), nullable=True)
    tipo = Column(String, nullable=False)           # 'DEBITO' ou 'CREDITO'
    valor = Column(Numeric(10, 2), nullable=False)
    data = Column(DateTime, default=datetime.now)
    descricao = Column(String)
    __table_args__ = (CheckConstraint(tipo.in_(['DEBITO', 'CREDITO'])),)

class ExtraFeira(Base):
    """Ganhos extras por feira (fígado, bucho, mocotó, etc.).
    Valor JÁ está no caixa — serve apenas para análise de composição do lucro."""
    __tablename__ = 'extras_feira'
    id = Column(Integer, primary_key=True)
    id_feira = Column(Integer, ForeignKey('feiras.id'), nullable=False)
    descricao = Column(String, nullable=False)
    valor = Column(Numeric(10, 2), nullable=False)

# 3. Criação e inicialização do banco
def criar_banco():
    Base.metadata.create_all(engine)
    session = Session()
    try:
        defaults = {
            'fator_quebra': 0.10,
            'threshold_fiado': 0.15,   # 15% do faturamento em fiado = alerta
        }
        for chave, valor in defaults.items():
            if not session.query(Configuracao).filter_by(chave=chave).first():
                session.add(Configuracao(chave=chave, valor=valor))
        session.commit()
        print("Banco de dados criado/atualizado com sucesso!")
    except Exception as e:
        session.rollback()
        print(f"Erro ao configurar banco: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    criar_banco()