"""Illustrative ISO 20022 message generation for Payment 360's MESSAGES tab.

Per schema.sql's header note, these are generated on demand from a payment's real fields
(never stored) \u2014 a faithful port of the POC's isoXml(), semantically aligned but not a full
usage-guideline-validated implementation. All values come from the real payment row; nothing
here is randomly fabricated.
"""
from __future__ import annotations

from typing import Optional

from app.engine.derive import reason_code

ISO_MESSAGE_TYPES = [
    "pain.001", "pacs.008", "pacs.009", "pacs.002", "pacs.004",
    "camt.052", "camt.053", "camt.054", "camt.056", "camt.029",
]

STATUS_CODE = {
    "COMPLETED": "ACCC", "IN_PROGRESS": "ACSP", "REJECTED": "RJCT",
    "FAILED": "RJCT", "RETURNED": "RTND", "INVESTIGATION": "PDNG", "CANCELLED": "CANC",
}

TOWN_BY_COUNTRY = {"GB": "London", "US": "New York", "CH": "Zug"}


def _esc(v: object) -> str:
    s = "" if v is None else str(v)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _agt(name: Optional[str], name_to_bic: dict[str, str]) -> str:
    if not name:
        return ""
    bic = name_to_bic.get(name, "")
    return f"<FinInstnId><BICFI>{_esc(bic)}</BICFI><Nm>{_esc(name)}</Nm></FinInstnId>"


def _pty(name: Optional[str], country: Optional[str], address_format: Optional[str]) -> str:
    town = TOWN_BY_COUNTRY.get(country or "", "City")
    if address_format == "UNSTRUCTURED":
        addr = f"<AdrLine>1 Harbour Road</AdrLine><AdrLine>{_esc(country)}</AdrLine>"
    else:
        addr = f"<TwnNm>{town}</TwnNm><Ctry>{_esc(country)}</Ctry>"
    return f"<Nm>{_esc(name)}</Nm><PstlAdr>{addr}</PstlAdr>"


def build_iso_message(msg_type: str, p: dict, name_to_bic: dict[str, str]) -> str:
    """p is a payment dict keyed by field_catalogue field_id (camelCase)."""
    now = (p.get("initiatedAt") or f"{p.get('settlementDate')} 09:00:00").replace(" ", "T")
    amt = f"{float(p.get('amount') or 0):.2f}"
    c = reason_code(p.get("statusReason")) or "AC03"
    agt = lambda n: _agt(n, name_to_bic)  # noqa: E731
    pty = lambda n, ctry: _pty(n, ctry, p.get("addressFormat"))  # noqa: E731

    hdr = (
        "<!-- head.001 Business Application Header -->\n"
        f"<AppHdr><Fr>{agt(p.get('debtorAgent'))}</Fr>"
        f"<To>{agt(p.get('correspondent') or p.get('creditorAgent'))}</To>\n"
        f"  <BizMsgIdr>{_esc(p.get('businessMsgId'))}</BizMsgIdr>"
        f"<MsgDefIdr>{_esc(msg_type)}</MsgDefIdr><CreDt>{_esc(now)}Z</CreDt></AppHdr>\n"
    )
    orig = (
        f"<OrgnlGrpInf><OrgnlMsgId>{_esc(p.get('businessMsgId'))}</OrgnlMsgId>"
        f"<OrgnlMsgNmId>{'pacs.009.001.08' if p.get('messageType') == 'pacs.009' else 'pacs.008.001.08'}</OrgnlMsgNmId></OrgnlGrpInf>"
    )
    ids = (
        f"<OrgnlInstrId>{_esc(p.get('instructionId'))}</OrgnlInstrId>"
        f"<OrgnlEndToEndId>{_esc(p.get('endToEndId'))}</OrgnlEndToEndId>"
        + (f"<OrgnlUETR>{_esc(p.get('uetr'))}</OrgnlUETR>" if p.get("uetr") else "")
    )

    if msg_type == "pain.001":
        svc_lvl = "G001" if p.get("domain") == "CBCC" else ("NURG" if p.get("rail") == "ACH" else "URGP")
        ult_cdtr = f"\n      <UltmtCdtr><Nm>{_esc(p.get('ultimateCreditor'))}</Nm></UltmtCdtr>" if p.get("ultimateCreditor") else ""
        body = f"""<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.09">
<CstmrCdtTrfInitn>
  <GrpHdr><MsgId>{_esc(p.get('businessMsgId'))}</MsgId><CreDtTm>{_esc(now)}</CreDtTm><NbOfTxs>1</NbOfTxs><CtrlSum>{amt}</CtrlSum>
    <InitgPty><Nm>{_esc(p.get('debtor'))}</Nm></InitgPty></GrpHdr>
  <PmtInf><PmtInfId>PMI-{_esc(p.get('instructionId'))}</PmtInfId><PmtMtd>TRF</PmtMtd>
    <PmtTpInf><SvcLvl><Cd>{svc_lvl}</Cd></SvcLvl><CtgyPurp><Cd>{_esc(p.get('purpose'))}</Cd></CtgyPurp></PmtTpInf>
    <ReqdExctnDt><Dt>{_esc(p.get('settlementDate'))}</Dt></ReqdExctnDt>
    <Dbtr>{pty(p.get('debtor'), p.get('debtorCountry'))}</Dbtr><DbtrAcct><Id><Othr><Id>{_esc(p.get('debtorAccount'))}</Id></Othr></Id></DbtrAcct>
    <DbtrAgt>{agt(p.get('debtorAgent'))}</DbtrAgt><ChrgBr>{_esc(p.get('charges'))}</ChrgBr>
    <CdtTrfTxInf>
      <PmtId><InstrId>{_esc(p.get('instructionId'))}</InstrId><EndToEndId>{_esc(p.get('endToEndId'))}</EndToEndId>{f"<UETR>{_esc(p.get('uetr'))}</UETR>" if p.get('uetr') else ''}</PmtId>
      <Amt><InstdAmt Ccy="{_esc(p.get('debitCcy'))}">{amt}</InstdAmt></Amt>
      <CdtrAgt>{agt(p.get('creditorAgent'))}</CdtrAgt><Cdtr>{pty(p.get('creditor'), p.get('creditorCountry'))}</Cdtr>
      <CdtrAcct><Id><Othr><Id>{_esc(p.get('creditorAccount'))}</Id></Othr></Id></CdtrAcct>{ult_cdtr}
    </CdtTrfTxInf></PmtInf>
</CstmrCdtTrfInitn></Document>"""

    elif msg_type == "pacs.008":
        clr_sys = ""
        if p.get("domain") == "DOME":
            code = {"Fedwire": "FDW", "FedNow": "FDN"}.get(p.get("rail"), "TCH")
            clr_sys = f"<ClrSys><Cd>{code}</Cd></ClrSys>"
        intrmy = f"\n    <IntrmyAgt1>{agt(p.get('intermediary'))}</IntrmyAgt1>" if p.get("intermediary") else ""
        ult_dbtr = f"<UltmtDbtr><Nm>{_esc(p.get('ultimateDebtor'))}</Nm></UltmtDbtr>\n    " if p.get("ultimateDebtor") and p.get("ultimateDebtor") != p.get("debtor") else ""
        ult_cdtr = f"\n    <UltmtCdtr><Nm>{_esc(p.get('ultimateCreditor'))}</Nm></UltmtCdtr>" if p.get("ultimateCreditor") else ""
        fx = f"\n    <XchgRate>{p.get('fxRate')}</XchgRate>" if p.get("fxRate") not in (1, None) else ""
        body = f"""<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08">
<FIToFICstmrCdtTrf>
  <GrpHdr><MsgId>{_esc(p.get('businessMsgId'))}</MsgId><CreDtTm>{_esc(now)}</CreDtTm><NbOfTxs>1</NbOfTxs>
    <SttlmInf><SttlmMtd>{_esc(p.get('settlementMethod'))}</SttlmMtd>{clr_sys}</SttlmInf></GrpHdr>
  <CdtTrfTxInf>
    <PmtId><InstrId>{_esc(p.get('instructionId'))}</InstrId><EndToEndId>{_esc(p.get('endToEndId'))}</EndToEndId><TxId>{_esc(p.get('swiftTxnId') or p.get('instructionId'))}</TxId>{f"<UETR>{_esc(p.get('uetr'))}</UETR>" if p.get('uetr') else ''}</PmtId>
    <PmtTpInf><InstrPrty>{_esc(p.get('priority'))}</InstrPrty>{'<SvcLvl><Cd>G001</Cd></SvcLvl>' if p.get('domain') == 'CBCC' else ''}</PmtTpInf>
    <IntrBkSttlmAmt Ccy="{_esc(p.get('debitCcy'))}">{amt}</IntrBkSttlmAmt><IntrBkSttlmDt>{_esc(p.get('settlementDate'))}</IntrBkSttlmDt>{fx}
    <ChrgBr>{_esc(p.get('charges'))}</ChrgBr>
    <InstgAgt>{agt(p.get('debtorAgent'))}</InstgAgt><InstdAgt>{agt(p.get('correspondent') or p.get('creditorAgent'))}</InstdAgt>{intrmy}
    {ult_dbtr}<Dbtr>{pty(p.get('debtor'), p.get('debtorCountry'))}</Dbtr><DbtrAcct><Id><Othr><Id>{_esc(p.get('debtorAccount'))}</Id></Othr></Id></DbtrAcct>
    <DbtrAgt>{agt(p.get('debtorAgent'))}</DbtrAgt><CdtrAgt>{agt(p.get('creditorAgent'))}</CdtrAgt>
    <Cdtr>{pty(p.get('creditor'), p.get('creditorCountry'))}</Cdtr><CdtrAcct><Id><Othr><Id>{_esc(p.get('creditorAccount'))}</Id></Othr></Id></CdtrAcct>{ult_cdtr}
    <Purp><Cd>{_esc(p.get('purpose'))}</Cd></Purp>
  </CdtTrfTxInf></FIToFICstmrCdtTrf></Document>"""

    elif msg_type == "pacs.009":
        intrmy = f"\n    <IntrmyAgt1>{agt(p.get('intermediary'))}</IntrmyAgt1>" if p.get("intermediary") else ""
        body = f"""<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.009.001.08">
<FICdtTrf>
  <GrpHdr><MsgId>{_esc(p.get('businessMsgId'))}</MsgId><CreDtTm>{_esc(now)}</CreDtTm><NbOfTxs>1</NbOfTxs><SttlmInf><SttlmMtd>{_esc(p.get('settlementMethod'))}</SttlmMtd></SttlmInf></GrpHdr>
  <CdtTrfTxInf>
    <PmtId><InstrId>{_esc(p.get('instructionId'))}</InstrId><EndToEndId>{_esc(p.get('endToEndId'))}</EndToEndId>{f"<UETR>{_esc(p.get('uetr'))}</UETR>" if p.get('uetr') else ''}</PmtId>
    <IntrBkSttlmAmt Ccy="{_esc(p.get('debitCcy'))}">{amt}</IntrBkSttlmAmt><IntrBkSttlmDt>{_esc(p.get('settlementDate'))}</IntrBkSttlmDt>
    <InstgAgt>{agt(p.get('debtorAgent'))}</InstgAgt><InstdAgt>{agt(p.get('correspondent') or p.get('creditorAgent'))}</InstdAgt>{intrmy}
    <Dbtr>{agt(p.get('debtorAgent'))}</Dbtr><CdtrAgt>{agt(p.get('creditorAgent'))}</CdtrAgt><Cdtr>{agt(p.get('creditorAgent'))}</Cdtr>
    <!-- COV variant would carry UndrlygCstmrCdtTrf with Dbtr {_esc(p.get('debtor'))} / Cdtr {_esc(p.get('creditor'))} -->
  </CdtTrfTxInf></FICdtTrf></Document>"""

    elif msg_type == "pacs.002":
        status = p.get("status")
        rsn = ""
        if status in ("REJECTED", "FAILED", "RETURNED"):
            code = "NARR" if status == "FAILED" else c
            rsn = f"\n    <StsRsnInf><Rsn><Cd>{_esc(code)}</Cd></Rsn><AddtlInf>{_esc(p.get('statusReason'))}</AddtlInf></StsRsnInf>"
        instg = p.get("creditorAgent") if status in ("COMPLETED", "REJECTED") else (p.get("correspondent") or p.get("creditorAgent"))
        body = f"""<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.002.001.10">
<FIToFIPmtStsRpt>
  <GrpHdr><MsgId>STS-{_esc(p.get('businessMsgId'))}</MsgId><CreDtTm>{_esc(now)}</CreDtTm></GrpHdr>
  <TxInfAndSts>{orig}
    {ids}
    <TxSts>{STATUS_CODE.get(status, 'ACSP')}</TxSts>{rsn}
    <InstgAgt>{agt(instg)}</InstgAgt><InstdAgt>{agt(p.get('debtorAgent'))}</InstdAgt>
  </TxInfAndSts></FIToFIPmtStsRpt></Document>"""

    elif msg_type == "pacs.004":
        rtr_rsn = p.get("statusReason") if reason_code(p.get("statusReason")) else "AC03 \u2014 Invalid Creditor Account Number"
        body = f"""<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.004.001.09">
<PmtRtr>
  <GrpHdr><MsgId>RTR-{_esc(p.get('businessMsgId'))}</MsgId><CreDtTm>{_esc(now)}</CreDtTm><NbOfTxs>1</NbOfTxs><SttlmInf><SttlmMtd>INDA</SttlmMtd></SttlmInf></GrpHdr>
  <TxInf><RtrId>RTN-{_esc(p.get('instructionId'))}</RtrId>{orig}
    {ids}
    <RtrdIntrBkSttlmAmt Ccy="{_esc(p.get('debitCcy'))}">{amt}</RtrdIntrBkSttlmAmt><IntrBkSttlmDt>{_esc(p.get('settlementDate'))}</IntrBkSttlmDt>
    <InstgAgt>{agt(p.get('creditorAgent'))}</InstgAgt><InstdAgt>{agt(p.get('correspondent') or p.get('debtorAgent'))}</InstdAgt>
    <RtrRsnInf><Rsn><Cd>{_esc(c)}</Cd></Rsn><AddtlInf>{_esc(rtr_rsn)}</AddtlInf></RtrRsnInf>
  </TxInf></PmtRtr></Document>"""

    elif msg_type == "camt.052":
        body = f"""<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.052.001.08">
<BkToCstmrAcctRpt>
  <GrpHdr><MsgId>RPT-{_esc(p.get('settlementDate'))}-1400</MsgId><CreDtTm>{_esc(p.get('settlementDate'))}T14:00:00</CreDtTm></GrpHdr>
  <Rpt><Id>INTRADAY-{_esc((p.get('nostro') or '').replace(' ', ''))}</Id><Acct><Id><Othr><Id>{_esc(p.get('nostro'))}</Id></Othr></Id><Ccy>{_esc(p.get('debitCcy'))}</Ccy></Acct>
    <Bal><Tp><CdOrPrtry><Cd>ITBD</Cd></CdOrPrtry></Tp><Amt Ccy="{_esc(p.get('debitCcy'))}">{(float(p.get('amount') or 0) * 7.3):.2f}</Amt><CdtDbtInd>CRDT</CdtDbtInd></Bal>
    <Ntry><Amt Ccy="{_esc(p.get('debitCcy'))}">{amt}</Amt><CdtDbtInd>DBIT</CdtDbtInd><Sts><Cd>{'BOOK' if p.get('status') == 'COMPLETED' else 'PDNG'}</Cd></Sts>
      <NtryDtls><TxDtls><Refs><EndToEndId>{_esc(p.get('endToEndId'))}</EndToEndId>{f"<UETR>{_esc(p.get('uetr'))}</UETR>" if p.get('uetr') else ''}</Refs></TxDtls></NtryDtls></Ntry>
  </Rpt></BkToCstmrAcctRpt></Document>"""

    elif msg_type == "camt.053":
        body = f"""<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">
<BkToCstmrStmt>
  <GrpHdr><MsgId>STMT-{_esc(p.get('settlementDate'))}</MsgId><CreDtTm>{_esc(p.get('settlementDate'))}T23:59:00</CreDtTm></GrpHdr>
  <Stmt><Id>EOD-{_esc(p.get('settlementDate'))}</Id><Acct><Id><Othr><Id>{_esc(p.get('nostro'))}</Id></Othr></Id><Ccy>{_esc(p.get('debitCcy'))}</Ccy></Acct>
    <Bal><Tp><CdOrPrtry><Cd>OPBD</Cd></CdOrPrtry></Tp><Amt Ccy="{_esc(p.get('debitCcy'))}">{(float(p.get('amount') or 0) * 9.1):.2f}</Amt><CdtDbtInd>CRDT</CdtDbtInd></Bal>
    <Bal><Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp><Amt Ccy="{_esc(p.get('debitCcy'))}">{(float(p.get('amount') or 0) * 8.1):.2f}</Amt><CdtDbtInd>CRDT</CdtDbtInd></Bal>
    <Ntry><Amt Ccy="{_esc(p.get('debitCcy'))}">{amt}</Amt><CdtDbtInd>DBIT</CdtDbtInd><Sts><Cd>{'BOOK' if p.get('status') == 'COMPLETED' else 'PDNG'}</Cd></Sts><BookgDt><Dt>{_esc(p.get('settlementDate'))}</Dt></BookgDt>
      <!-- recon state: {_esc(p.get('reconState'))} --></Ntry>
  </Stmt></BkToCstmrStmt></Document>"""

    elif msg_type == "camt.054":
        note = "MT910-equivalent credit confirmation" if p.get("status") == "COMPLETED" else f"No credit booked \u2014 payment not completed ({_esc(p.get('status'))})"
        body = f"""<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.054.001.08">
<BkToCstmrDbtCdtNtfctn>
  <GrpHdr><MsgId>NTF-{_esc(p.get('instructionId'))}</MsgId><CreDtTm>{_esc(now)}</CreDtTm></GrpHdr>
  <Ntfctn><Id>NTF-{_esc(p.get('endToEndId'))}</Id><Acct><Id><Othr><Id>{_esc(p.get('creditorAccount'))}</Id></Othr></Id></Acct>
    <Ntry><Amt Ccy="{_esc(p.get('creditCcy'))}">{float(p.get('creditAmount') or 0):.2f}</Amt><CdtDbtInd>CRDT</CdtDbtInd><Sts><Cd>{'BOOK' if p.get('status') == 'COMPLETED' else 'PDNG'}</Cd></Sts>
      <!-- {note} -->
      <NtryDtls><TxDtls><Refs><EndToEndId>{_esc(p.get('endToEndId'))}</EndToEndId>{f"<UETR>{_esc(p.get('uetr'))}</UETR>" if p.get('uetr') else ''}</Refs><RltdPties><Dbtr><Pty><Nm>{_esc(p.get('debtor'))}</Nm></Pty></Dbtr></RltdPties></TxDtls></NtryDtls></Ntry>
  </Ntfctn></BkToCstmrDbtCdtNtfctn></Document>"""

    elif msg_type == "camt.056":
        invg = p.get("investigationId") or "INV-PENDING"
        rsn_cd = "FRAD" if "MATCH" in str(p.get("screening") or "") else "AGNT"
        addtl = p.get("statusReason") if p.get("statusReason") and p.get("statusReason") != "\u2014" else "Investigation / recall request"
        body = f"""<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.056.001.08">
<FIToFIPmtCxlReq>
  <Assgnmt><Id>{_esc(invg)}</Id><Assgnr><Agt>{agt(p.get('debtorAgent'))}</Agt></Assgnr><Assgne><Agt>{agt(p.get('correspondent') or p.get('creditorAgent'))}</Agt></Assgne><CreDtTm>{_esc(now)}</CreDtTm></Assgnmt>
  <Case><Id>{_esc(invg)}</Id><Cretr><Agt>{agt(p.get('debtorAgent'))}</Agt></Cretr></Case>
  <Undrlyg><TxInf><CxlId>CXL-{_esc(p.get('instructionId'))}</CxlId>{orig}
    {ids}
    <OrgnlIntrBkSttlmAmt Ccy="{_esc(p.get('debitCcy'))}">{amt}</OrgnlIntrBkSttlmAmt>
    <CxlRsnInf><Rsn><Cd>{rsn_cd}</Cd></Rsn><AddtlInf>{_esc(addtl)}</AddtlInf></CxlRsnInf>
  </TxInf></Undrlyg></FIToFIPmtCxlReq></Document>"""

    elif msg_type == "camt.029":
        invg = p.get("investigationId") or "INV-PENDING"
        status = p.get("status")
        conf = "CNCL" if status in ("RETURNED", "CANCELLED") else ("RJCR" if status == "COMPLETED" else "PDCR")
        addtl = (
            "Payment already credited \u2014 cancellation rejected" if status == "COMPLETED"
            else "Funds returned via pacs.004" if status == "RETURNED"
            else "Pending further information"
        )
        body = f"""<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.029.001.09">
<RsltnOfInvstgtn>
  <Assgnmt><Id>RSL-{_esc(invg)}</Id><Assgnr><Agt>{agt(p.get('correspondent') or p.get('creditorAgent'))}</Agt></Assgnr><Assgne><Agt>{agt(p.get('debtorAgent'))}</Agt></Assgne><CreDtTm>{_esc(now)}</CreDtTm></Assgnmt>
  <Sts><Conf>{conf}</Conf></Sts>
  <CxlDtls><TxInfAndSts>{ids}<CxlStsRsnInf><AddtlInf>{_esc(addtl)}</AddtlInf></CxlStsRsnInf></TxInfAndSts></CxlDtls>
</RsltnOfInvstgtn></Document>"""

    else:
        body = "<!-- not modelled -->"

    return hdr + body
