import { expect, it } from 'vitest'
import { withOverlapDrawings } from './overlapOutput'
import type { IndicatorInstance } from '../state/indicators'
import type { DrawingObj } from '../api/types'
const inst={uid:'i',scriptId:'smc_ict_scalping_master',inputs:{}} as IndicatorInstance
const marker:DrawingObj={kind:'label',id:-30000,x:900,y:100,text:'↶ H',yloc:'belowbar',style:'label_up',
  color:'#089981FF',text_color:'#FFFFFFFF',size:'tiny',tooltip:'Confirmed',group:'badge_overlap',created_at:960}
it('paints confirmed reactions without waiting for a full indicator output',()=>{
  const output=withOverlapDrawings(inst,null,[marker])!
  expect(output.drawings).toEqual([marker])
  expect(output.signals).toEqual([])
  expect(output.times).toEqual([])
  expect(output.last_confirmed_time).toBeNull()
})
it('preserves full indicator output when it arrives and does not duplicate annotations',()=>{
  const base=withOverlapDrawings(inst,null,[marker])!
  const output=withOverlapDrawings(inst,{...base,drawings:[]},[marker])!
  expect(output.drawings).toEqual([marker])
  expect(withOverlapDrawings(inst,output,[])).toBe(output)
})
it('does not fabricate an indicator output when no annotations are available',()=>{
  expect(withOverlapDrawings(inst,null,[])).toBeNull()
})
