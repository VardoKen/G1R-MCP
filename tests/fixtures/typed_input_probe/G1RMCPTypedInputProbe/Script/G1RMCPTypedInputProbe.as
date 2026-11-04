// USTRUCT/UPROPERTY: Modkit Script/GAS/Abilities/Combat/AttackLoop/Base/GA_AttackLoop.as.
// float64: Modkit Script/AI/States/FightAI/CombatState/AIState_Combat.as.
// UFUNCTION и TArray<int> по значению: Modkit CrimeProcessingSubsystem.as, ForgiveCrimesByID.
// Сначала подтвердить фактические StructProperty/ArrayProperty и поля через reflection.
// Проверено в игре: dev.102, Steam build 26394867; массивы 0/6/-1, структуры 1009/-9/0.
USTRUCT()
struct FG1RMCPScalarInput
{
    UPROPERTY()
    int Integer = 0;

    UPROPERTY()
    float64 Number = 0.0;

    UPROPERTY()
    bool Flag = false;
}

UCLASS()
class UG1RMCPTypedInputProbe : UObject
{
    UFUNCTION(BlueprintCallable)
    int ReadMarker()
    {
        return 71;
    }

    // [] -> 0; [1, 2, 3] -> 6; [2147483647, -2147483648] -> -1.
    // Наборы приёмки не должны переполнять int при промежуточном сложении.
    UFUNCTION(BlueprintCallable)
    int SumIntArray(TArray<int> Values)
    {
        int Sum = 0;
        for (int Index = 0; Index < Values.Num(); Index++)
        {
            Sum += Values[Index];
        }
        return Sum;
    }

    // {Integer:7, Number:2.75, Flag:true} -> 1009;
    // {Integer:-7, Number:-2.75, Flag:false} -> -9; нулевые поля -> 0.
    UFUNCTION(BlueprintCallable)
    int ScalarStructMarker(FG1RMCPScalarInput Value)
    {
        int Marker = Value.Integer + int(Value.Number);
        if (Value.Flag)
        {
            Marker += 1000;
        }
        return Marker;
    }
}
