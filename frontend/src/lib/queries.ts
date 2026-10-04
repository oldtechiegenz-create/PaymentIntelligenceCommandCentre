import { useQuery } from '@tanstack/react-query'
import { getDashboard, getDrilldownCustomers, getDrilldownRails, getFields, getKuberAgents, getMandates, getPaymentDetail, getScenarios, getSimStatus, getSpeeds, searchPayments, type OperatingMode, type PaymentSearchParams } from './api'

export function useDashboard(mode: OperatingMode = 'ALL', date?: string, options?: { enabled?: boolean }) {
  return useQuery({ queryKey: ['dashboard', mode, date], queryFn: () => getDashboard(mode, date), enabled: options?.enabled ?? true })
}

export function useFields() {
  return useQuery({ queryKey: ['fields'], queryFn: getFields, staleTime: Infinity })
}

export function usePayments(params: PaymentSearchParams) {
  return useQuery({ queryKey: ['payments', params], queryFn: () => searchPayments(params) })
}

export function usePaymentDetail(paymentId: string | null) {
  return useQuery({
    queryKey: ['payment-detail', paymentId],
    queryFn: () => getPaymentDetail(paymentId as string),
    enabled: !!paymentId,
  })
}

export function useScenarios() {
  return useQuery({ queryKey: ['scenarios'], queryFn: getScenarios, staleTime: Infinity })
}

export function useSpeeds() {
  return useQuery({ queryKey: ['speeds'], queryFn: getSpeeds, staleTime: Infinity })
}

export function useSimStatus(paymentId: string | null, scenarioCode?: string) {
  return useQuery({
    queryKey: ['sim-status', paymentId, scenarioCode],
    queryFn: () => getSimStatus(paymentId as string, scenarioCode),
    enabled: !!paymentId,
  })
}

export function useDrilldownCustomers(date?: string) {
  return useQuery({ queryKey: ['drilldown-customers', date], queryFn: () => getDrilldownCustomers(date) })
}

export function useDrilldownRails(date?: string) {
  return useQuery({ queryKey: ['drilldown-rails', date], queryFn: () => getDrilldownRails(date) })
}

export function useMandates() {
  return useQuery({ queryKey: ['mandates'], queryFn: getMandates })
}

export function useKuberAgents() {
  return useQuery({ queryKey: ['kuber-agents'], queryFn: getKuberAgents, staleTime: Infinity })
}
