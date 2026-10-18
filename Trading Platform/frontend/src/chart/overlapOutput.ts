import type { DrawingObj, IndicatorOutput } from '../api/types'
import type { IndicatorInstance } from '../state/indicators'

/** Confirmed overlap annotations can paint before the full SMC indicator finishes. */
export function withOverlapDrawings(inst:IndicatorInstance, output:IndicatorOutput|null, drawings:DrawingObj[]):IndicatorOutput|null {
  if (!drawings.length) return output
  if (output) return {...output,drawings:[...output.drawings,...drawings]}
  // Drawing-only layer: no fabricated indicator values, signals or candle confirmations.
  return {times:[],plots:{},plot_colors:{},shapes:{},bar_colors:[],candle_colors:[],tables:[],warnings:[],
    drawings,signals:[],bars_processed:0,last_confirmed_time:null,provisional_bar:false,warmup_bars:0,
    script_id:inst.scriptId,inputs:inst.inputs}
}
