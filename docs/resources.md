> ## Documentation Index
> Fetch the complete documentation index at: https://docs.battlecode.cam/llms.txt
> Use this file to discover all available pages before exploring further.

# Resources

> Titanium, axionite, and the cost scaling formula.

export const DenseTable = ({children}) => <div className="dense-table">{children}</div>;

## Titanium

<img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/resources/titanium.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=99cae2a87a4dbda333098af04468f3f7" alt="Titanium" style={{ width: 48, float: "right", marginLeft: 16 }} width="512" height="512" data-path="images/resources/titanium.png" />

The primary resource used to construct most buildings. Each team starts with
**500 titanium** and gains **10 passive titanium every 4 rounds**.

Titanium is harvested from titanium ore deposits and delivered to the core via conveyors.

## Axionite

Axionite comes in two forms:

<CardGroup cols={2}>
  <Card title="Raw axionite" icon="gem">
    <img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/resources/axionite-raw.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=38b077f489b1034e7689f88bd83f2ca6" alt="Raw axionite" style={{ width: 32 }} width="512" height="512" data-path="images/resources/axionite-raw.png" />

    Mined from axionite ore deposits. When fed to a turret or core, it is **destroyed**. You must refine it first for advanced uses.
  </Card>

  <Card title="Refined axionite" icon="flask-vial">
    <img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/resources/axionite-refined.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=3829da5cf16f1707f8ed08407ba149cc" alt="Refined axionite" style={{ width: 32 }} width="512" height="512" data-path="images/resources/axionite-refined.png" />

    Produced by [axionite foundries](/spec/harvester-and-foundry#axionite-foundry) from raw axionite + titanium. Used for powerful units and advanced infrastructure.
  </Card>
</CardGroup>

<Info>
  Whenever "axionite" is mentioned in the spec without qualification, it refers to **refined axionite**.
</Info>

## Conversion

The core can convert refined axionite from the global resource pool into
titanium with `c.convert(amount)`.

$$
1 \text{ Ax} \rightarrow 4 \text{ Ti}
$$

Converted axionite is removed from the Ax collected stat and added to the Ti
collected stat.

## Resource distribution

Resources are stored and moved in **stacks of 10**. At the end of each round, buildings that output resources send them to adjacent buildings that accept them.

Each stored stack also has a **resource ID**. You can query the stack currently sitting in a conveyor or other storage building with `c.get_stored_resource_id(...)`.

<Warning>
  Resources can be outputted to buildings belonging to the **opposing team**.
</Warning>

See [conveyors](/spec/conveyors), [harvester & foundry](/spec/harvester-and-foundry), and [turrets](/spec/turrets) for details on input/output directions.

## Cost scaling

Every building and unit you construct increases the cost of future builds. The cost of every building and unit is:

$$
\text{cost} = \lfloor \text{scale} \times \text{base cost} \rfloor
$$

Where scale starts at 1.0 and increases **additively** with each entity built — two gunners at +10% each give 1.2x, not 1.21x. You can query the current scale with `c.get_scale_percent()` which returns it as a percentage (100.0 at base).

<DenseTable>
  <table>
    <thead><tr><th>Entity</th><th>Scale increase</th></tr></thead>

    <tbody>
      <tr><td>Road</td><td>+0.5%</td></tr>
      <tr><td>Conveyor, splitter, armoured conveyor, barrier</td><td>+1%</td></tr>
      <tr><td>Bridge</td><td>+10%</td></tr>
      <tr><td>Harvester</td><td>+5%</td></tr>
      <tr><td>Gunner, breach, launcher</td><td>+10%</td></tr>
      <tr><td>Builder bot, sentinel</td><td>+20%</td></tr>
      <tr><td>Axionite foundry</td><td>+100%</td></tr>
    </tbody>
  </table>
</DenseTable>

When an entity is destroyed, its scaling contribution is removed — costs go back down.

<Tip>
  Every entity you build makes the next one more expensive. Be efficient with what you build!
</Tip>


Built with [Mintlify](https://mintlify.com).